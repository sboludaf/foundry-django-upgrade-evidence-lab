import copy
import hashlib
import json
import os
import time
import uuid

from .domain import compare, validate_evidence, version, decision_warnings
from .errors import AppError
from .integrations import (agents, read_grounding, call_decision, decision_body,
    prompt_project, prompt_grounding_tool, prompt_application_agent, response_client, read_response)
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
    tool = prompt_grounding_tool()  # Missing Bing must not consume quota or create agents.
    backend.acquire(job_id)
    step_id = job_id + ":grounding"
    backend.reserve(job_id, step_id, models=5, searches=3)
    job["stage"], job["effect"] = "grounding_creating_agent", step_id
    backend.save_job(job, etag)
    project = prompt_project()
    try:
        agent = prompt_application_agent(project, tool)
        job, etag = backend.job(job_id, owner)
        job.update(agent_id=agent.id, agent_name=agent.name, agent_version=agent.version,
                   grounding_api="responses", stage="grounding_creating_conversation")
        backend.save_job(job, etag)
        context_sources = []
        target_branch = ".".join(job["version"].split(".")[:2])
        for doc in job["sources"]:
            limit = 1100 if "security/" in doc["url"] else 600
            if "/releases/" in doc["url"] and "security/" not in doc["url"]:
                limit = 1100 if ("/releases/" + target_branch + ".") in doc["url"] else 450
            if "pypi.org" in doc["url"]:
                limit = 100
            context_sources.append({"url": doc["url"], "excerpt": doc["excerpt"][:limit]})
        context = json.dumps({"as_of": time.strftime("%Y-%m-%d", time.gmtime()),
                             "version": job["version"], "batch": job["batch_hash"],
                             "sources": context_sources}, ensure_ascii=False)
        if len(context.encode()) > 8_000:
            raise AppError("grounding_input_limit", "El lote es demasiado grande para el presupuesto de entrada.", 413)
        client = response_client(project)
        conversation = client.conversations.create(metadata={"job": job_id})
        job, etag = backend.job(job_id, owner)
        job["conversation_id"], job["stage"] = conversation.id, "grounding_sending"
        backend.save_job(job, etag)
        # Persist the response ID at the first streamed event. Background work
        # survives disconnects. A lost POST without an ID is blocked, not resent.
        with client.responses.create(input=context, conversation=conversation.id,
                extra_body={"agent_reference": {"name": agent.name, "version": agent.version, "type": "agent_reference"}},
                background=True, store=True, stream=True, max_output_tokens=1000,
                max_tool_calls=1, parallel_tool_calls=False, metadata={"job": job_id},
                tool_choice="required") as stream:
            for event in stream:
                if event.type == "response.created":
                    job, etag = backend.job(job_id, owner)
                    job["response_id"], job["stage"] = event.response.id, "grounding_running"
                    backend.save_job(job, etag)
                    break
            else:
                raise AppError("grounding_uncertain", "La respuesta no confirmó su ID; no se reenviará.", 502)
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
    if (job["stage"] == "uncertain" and job.get("response_id")
            and job.get("effect") == job_id + ":grounding"):
        job["stage"] = "grounding_running"
        backend.save_job(job, etag)
        job, etag = backend.job(job_id, owner)
    if job["stage"] == "uncertain" and job.get("thread_id") and not job.get("run_id"):
        # A lost response to runs.create is reconciled by metadata, never repeated.
        matches = [r for r in agents().runs.list(thread_id=job["thread_id"], limit=100)
                   if (r.metadata or {}).get("job") == job_id]
        if len(matches) == 1:
            job["run_id"], job["stage"] = matches[0].id, "grounding_running"
            backend.save_job(job, etag)
            job, etag = backend.job(job_id, owner)
    if job["stage"] not in ("grounding_running", "grounding_failed"):
        return job
    try:
        result = (read_response(response_client(prompt_project()), job)
                  if job.get("grounding_api") == "responses" else read_grounding(agents(), job))
        if result is None:
            return job
        job["grounding"] = result
        job["original"] = result["extraction"]
        try:
            job["original_validated"] = validate_evidence(job["original"], job["sources"])
            job["original_validation"] = {"valid": True}
        except AppError as validation:
            job["original_validated"] = None
            job["original_validation"] = {"valid": False, "code": validation.code, "message": str(validation)}
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
    state = {"package": "Django", "version": job["version"], "review": job["reviewed"],
             "comparison": {"issues": [{"cve": i["cve"], "state": i["state"]} for i in job["comparison"]["issues"]],
                            "unresolved": job["comparison"]["unresolved"]},
             "batch_hash": job["batch_hash"]}
    decision_body(state)  # Configuration/size failures must not consume a reservation.
    backend.acquire(job_id)
    step = job_id + ":decision:" + str(job["review_version"])
    backend.reserve(job_id, step, 1, 0)
    job["stage"], job["effect"] = "decision_sending", step
    backend.save_job(job, etag)
    try:
        response = call_decision(state)
        job, etag = backend.job(job_id, owner)
        warnings = decision_warnings(job["comparison"], response)
        job["decision"] = {"response": response, "review_version": job["review_version"],
                           "scale": [0, 3], "policy": "Reference only; no automatic updates; thresholds uncalibrated",
                           "warnings": warnings,
                           "actionable": not warnings and job["comparison"]["actionable"] and response["answers"]["reason"]["choice"] != "insufficient-evidence"}
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
