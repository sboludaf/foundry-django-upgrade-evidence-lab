import copy
import hashlib
import json
import os
import time
import uuid

from .domain import compare, validate_evidence, version
from .errors import AppError
from .integrations import INSTRUCTIONS, agents, grounding_tool, read_grounding, call_decision
from .repository import store
from .sources import collect


def create(owner, installed, key):
    installed = version(installed)
    try:
        job_id = str(uuid.UUID(key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AppError("invalid_input", "La solicitud necesita un ID UUID estable.", 400) from exc
    backend = store()
    previous, _ = backend.get("jobs", job_id)
    if previous:
        if previous["owner"] != owner or previous["version"] != installed:
            raise AppError("id_conflict", "El ID ya pertenece a otra solicitud.")
        return previous
    job = {"id": job_id, "owner": owner, "package": "Django", "version": installed,
           "stage": "created", "sources": [], "original": None, "reviewed": None,
           "review_version": 0, "changes": [], "decision": None, "decision_stale": False,
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    backend.save_job(job, None)
    return job


def prepare(owner, job_id):
    backend = store()
    job, etag = backend.job(job_id, owner)
    if job["stage"] not in ("created", "source_failed"):
        return job
    backend.acquire(job_id)
    job["stage"] = "fetching_sources"
    backend.save_job(job, etag)
    job, etag = backend.job(job_id, owner)
    try:
        job["sources"] = collect(job["version"], backend, job_id)
        job["batch_hash"] = hashlib.sha256(json.dumps([
            {"url": d["url"], "sha256": d["sha256"]} for d in job["sources"]
        ], sort_keys=True).encode()).hexdigest()
        job["stage"] = "sources_ready"
        job.pop("error", None)
    except AppError as exc:
        job["stage"], job["error"] = "source_failed", {"code": exc.code, "message": str(exc)}
        backend.save_job(job, etag)
        backend.release(job_id)
        raise
    backend.save_job(job, etag)
    return job


def ground(owner, job_id):
    backend = store()
    job, etag = backend.job(job_id, owner)
    if job["stage"] != "sources_ready":
        raise AppError("stage_locked", "La etapa ya se inició o el lote no está listo. Reconcilia antes de continuar.")
    tool = grounding_tool()  # Missing Bing must not consume quota or create agents.
    backend.acquire(job_id)
    step_id = job_id + ":grounding"
    backend.reserve(job_id, step_id, models=5, searches=3)
    job["stage"], job["effect"] = "grounding_creating_agent", step_id
    backend.save_job(job, etag)
    client = agents()
    try:
        agent = client.create_agent(model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                                    name="django-" + job_id, instructions=INSTRUCTIONS, tools=tool.definitions)
        job, etag = backend.job(job_id, owner)
        job["agent_id"], job["stage"] = agent.id, "grounding_creating_thread"
        backend.save_job(job, etag)
        context = json.dumps({"version": job["version"], "batch": job["batch_hash"],
                             "sources": [{"url": d["url"], "excerpt": d["excerpt"][:1800]}
                                         for d in job["sources"]]}, ensure_ascii=False)
        if len(context.encode()) > 14_000:
            raise AppError("grounding_input_limit", "El lote es demasiado grande para el presupuesto de entrada.", 413)
        thread = client.threads.create(messages=[{"role": "user", "content": context}])
        job, etag = backend.job(job_id, owner)
        job["thread_id"], job["stage"] = thread.id, "grounding_sending"
        backend.save_job(job, etag)
        run = client.runs.create(thread_id=thread.id, agent_id=agent.id,
                                max_prompt_tokens=6000, max_completion_tokens=1000,
                                parallel_tool_calls=False, metadata={"job": job_id},
                                tool_choice={"type": "bing_custom_search"})
        job, etag = backend.job(job_id, owner)
        job["run_id"], job["stage"] = run.id, "grounding_running"
        backend.save_job(job, etag)
        return job
    except Exception as exc:
        job, etag = backend.job(job_id, owner)
        job["uncertain_stage"], job["stage"] = job["stage"], "uncertain"
        job["error"] = {"code": "grounding_uncertain", "message": "Etapa no confirmada. Reconcilia su ID antes de repetir."}
        backend.save_job(job, etag)
        if isinstance(exc, AppError):
            raise
        raise AppError("grounding_uncertain", job["error"]["message"], 502) from exc


def reconcile(owner, job_id):
    backend = store()
    job, etag = backend.job(job_id, owner)
    if job["stage"] == "uncertain" and job.get("thread_id") and not job.get("run_id"):
        # A lost response to runs.create is reconciled by metadata, never repeated.
        matches = [r for r in agents().runs.list(thread_id=job["thread_id"], limit=100)
                   if (r.metadata or {}).get("job") == job_id]
        if len(matches) == 1:
            job["run_id"], job["stage"] = matches[0].id, "grounding_running"
            backend.save_job(job, etag)
            job, etag = backend.job(job_id, owner)
    if job["stage"] != "grounding_running":
        return job
    try:
        result = read_grounding(agents(), job)
        if result is None:
            return job
        job["grounding"] = result
        job["original"] = result["extraction"]
        job["original_validated"] = validate_evidence(job["original"], job["sources"])
        job["stage"] = "awaiting_review"
        job.pop("error", None)
        backend.save_job(job, etag)
        backend.release(job_id)
    except AppError as exc:
        job["stage"], job["error"] = "grounding_failed", {"code": exc.code, "message": str(exc)}
        backend.save_job(job, etag)
        backend.release(job_id)
        raise
    return job


def review(owner, job_id, evidence, reason, expected_review):
    backend = store()
    job, etag = backend.job(job_id, owner)
    if job["stage"] not in ("awaiting_review", "reviewed", "complete", "decision_failed"):
        raise AppError("stage_locked", "No hay extracción completa pendiente de revisión.")
    if expected_review != job["review_version"]:
        raise AppError("concurrent_change", "La revisión ha cambiado. Recarga el informe.")
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
        raise AppError("review_reason", "Describe el motivo de confirmación o corrección (8–500 caracteres).", 400)
    validated = validate_evidence(evidence, job["sources"])
    job["review_version"] += 1
    job["reviewed"] = validated
    job["changes"].append({"review_version": job["review_version"], "reason": reason.strip(),
                           "at": int(time.time()), "evidence_hash": hashlib.sha256(json.dumps(validated, sort_keys=True).encode()).hexdigest()})
    job["comparison"] = compare(job["version"], validated)
    job["decision_stale"] = job["decision"] is not None
    job["stage"] = "reviewed"
    backend.save_job(job, etag)
    return job


def decide(owner, job_id):
    backend = store()
    job, etag = backend.job(job_id, owner)
    if job["stage"] != "reviewed":
        raise AppError("review_required", "Confirma la revisión antes de pedir una decisión.")
    if not job.get("grounding", {}).get("searches"):
        raise AppError("grounding_required", "La decisión requiere grounding Bing real acreditado.", 503)
    backend.acquire(job_id)
    step = job_id + ":decision:" + str(job["review_version"])
    backend.reserve(job_id, step, 1, 0)
    job["stage"], job["effect"] = "decision_sending", step
    backend.save_job(job, etag)
    try:
        response = call_decision({"package": "Django", "version": job["version"],
                                  "review": job["reviewed"], "comparison": job["comparison"],
                                  "batch_hash": job["batch_hash"]})
        job, etag = backend.job(job_id, owner)
        job["decision"] = {"response": response, "review_version": job["review_version"],
                           "scale": [0, 3], "policy": "Reference only; no automatic updates; thresholds uncalibrated",
                           "actionable": job["comparison"]["actionable"] and response["answers"]["reason"]["choice"] != "insufficient-evidence"}
        job["decision_stale"], job["stage"] = False, "complete"
        backend.save_job(job, etag)
        backend.release(job_id)
        return job
    except AppError as exc:
        job, etag = backend.job(job_id, owner)
        # Decision API offers no replay-safe recovery endpoint for an unconfirmed POST.
        job["stage"] = "uncertain" if exc.code in ("decision_uncertain", "decision_schema", "token_limit") else "decision_failed"
        job["error"] = {"code": exc.code, "message": str(exc)}
        backend.save_job(job, etag)
        if job["stage"] != "uncertain":
            backend.release(job_id)
        raise


def abandon(owner, job_id):
    backend = store()
    job, etag = backend.job(job_id, owner)
    if job["stage"] not in ("created", "source_failed", "sources_ready", "awaiting_review", "reviewed"):
        raise AppError("stage_locked", "No se puede cerrar una etapa activa o incierta sin conciliarla.")
    job["stage"] = "closed"
    backend.save_job(job, etag)
    backend.release(job_id)
    return job
