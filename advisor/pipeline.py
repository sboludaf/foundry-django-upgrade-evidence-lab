import copy
import hashlib
import hmac
import json
import os
import time
import uuid

from .domain import compare, validate_evidence, version, decision_warnings
from .errors import AppError, ResponseLookupError
from .recovery import mark_lookup_failure
from .integrations import (agents, read_grounding, call_decision, decision_body,
    prompt_project, prompt_grounding_tool, prompt_application_agent, response_client, read_response, response_snapshot)
from .repository import store
from .sources import collect
from .extraction import TARGETS
from .extraction import release_sentence
from .closure import CLOSABLE, CLOSED, state_token, provider_outcome


def grounding_context(job):
    """Keep CVE membership and GDAL conditions, rather than truncating each page's start."""
    targets = {"CVE-2026-108147", "CVE-2026-113733", "CVE-2026-114582"}
    sources = []
    for doc in job["sources"]:
        fragments = doc.get("fragments", [doc["excerpt"]])
        if "security/" in doc["url"]:
            selected = fragments[:3]
        elif "/weblog/" in doc["url"]:
            selected = [f for f in fragments if "Full protection requires GDAL" in f]
        elif "/releases/" in doc["url"]:
            selected = fragments[:1]
        else:
            selected = fragments[:1]
        item = {"url": doc["url"], "excerpts": selected,
                "documented_cves": sorted(targets.intersection(doc.get("cves", [])))}
        if "/releases/" in doc["url"]:
            fixed = doc["url"].rstrip("/").rsplit("/", 1)[-1]
            found = next((f for text in fragments if (f := release_sentence(text, fixed))), None)
            if found:
                item["documented_exact_patch"] = {"affected_from": found[1], "affected_to": found[1],
                    "fixed": fixed, "quote": found[0]}
        sources.append(item)
    context = json.dumps({"as_of": time.strftime("%Y-%m-%d", time.gmtime()),
                          "version": job["version"], "batch": job["batch_hash"],
                          "sources": sources}, ensure_ascii=False)
    if len(context.encode()) > 8_000:
        raise AppError("grounding_input_limit", "El lote es demasiado grande para el presupuesto de entrada.", 413)
    return context


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
        context = grounding_context(job)
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
                    if job.get('response_id') and job['response_id'] != event.response.id:
                        raise AppError('grounding_uncertain', 'La etapa devolvió otro ID; conserva el original.', 502)
                    job["response_id"], job["stage"] = event.response.id, "grounding_running"
                    job['response_context'] = {'project_endpoint': os.environ['AZURE_AI_PROJECT_ENDPOINT'],
                                               'base_url': str(client.base_url)}
                    backend.save_job(job, etag)
                elif event.type in ('response.completed', 'response.failed', 'response.incomplete'):
                    job, etag = backend.job(job_id, owner)
                    if not job.get('response_id'):
                        raise AppError('grounding_uncertain', 'Falta el ID inicial; no se reenviará.', 502)
                    job['response_snapshot'] = response_snapshot(event.response, job['response_id'])
                    backend.save_job(job, etag)
                    break
            else:
                raise AppError("grounding_uncertain", "El stream terminó sin respuesta final; conserva el ID y concilia sin reenviar.", 502)
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
    if job.get('recovery', {}).get('blocked'):
        raise AppError('grounding_recovery_required', job['error']['message'], 409)
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
        result = (read_response(None if job.get('response_snapshot') else response_client(prompt_project()), job)
                  if job.get("grounding_api") == "responses" else read_grounding(agents(), job))
        if result is None:
            return job
        job = apply_grounding_result(job, result)
        backend.save_job(job, etag)
        backend.release(job_id)
    except ResponseLookupError as exc:
        job = mark_lookup_failure(job, exc.diagnostic)
        backend.save_job(job, etag)
        # A missing/read-failed response is not a terminal inference. Keep both
        # the active-job lock and its entire original reservation.
        raise
    except AppError as exc:
        job["stage"], job["error"] = "grounding_failed", {"code": exc.code, "message": str(exc)}
        backend.save_job(job, etag)
        backend.release(job_id)
        raise
    return job


def apply_grounding_result(job, result):
    job = copy.deepcopy(job)
    job['grounding'] = result
    job['original'] = result['extraction']
    try:
        job['original_validated'] = validate_evidence(job['original'], job['sources'])
        job['original_validation'] = {'valid': True}
    except AppError as validation:
        job['original_validated'] = None
        job['original_validation'] = {'valid': False, 'code': validation.code, 'message': str(validation)}
    job['stage'] = 'awaiting_review'
    job.pop('error', None)
    job.pop('recovery', None)
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
    expected = set(TARGETS).intersection(cve for doc in job["sources"] for cve in doc.get("cves", []))
    if not expected.issubset({issue["cve"] for issue in validated["issues"]}):
        raise AppError("invalid_evidence", "Conserva todos los avisos del lote; una rama ausente sigue sin resolver.", 422)
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
    scoped_review = copy.deepcopy(job["reviewed"])
    for issue in scoped_review["issues"]:
        issue["branches"] = [b for b in issue["branches"] if b["branch"] == job["version"].rsplit(".", 1)[0]]
    state = {"package": "Django", "version": job["version"], "review": scoped_review,
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


def close_unverified(owner, job_id, reason, confirmed, expected_state, key):
    """Close only this report/lock while retaining every effect and reservation.

    Saving the job precedes releasing its lock. If release fails, the same
    request ID may safely resume that last step; it never calls a provider.
    """
    backend = store()
    job, etag = backend.job(job_id, owner)
    if confirmed is not True:
        raise AppError("closure_confirmation", "Confirma que el informe seguirá sin verificar y que puede haber consumo anterior o un segundo cargo.", 400)
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
        raise AppError("closure_reason", "Explica por qué cierras sin verificar (8–500 caracteres).", 400)
    try:
        key = str(uuid.UUID(key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AppError("invalid_input", "El cierre necesita un ID UUID estable.", 400) from exc
    previous = job.get("unverified_closure") or {}
    if job["stage"] in CLOSED:
        # Historical operator closures remain readable and closed. A new close
        # is not represented as a second disposition or a successful analysis.
        if previous and previous.get("id") != key:
            raise AppError("already_closed", "El informe ya está cerrado sin verificar. Consulta su cierre; prepara otro análisis con un ID nuevo.", 409)
        backend.release(job_id)
        return job
    if (not isinstance(expected_state, str) or len(expected_state) != 64
            or any(c not in "0123456789abcdef" for c in expected_state)
            or not hmac.compare_digest(expected_state, state_token(job))):
        raise AppError("concurrent_change", "El informe cambió. Recárgalo y confirma el cierre sobre su estado actual.", 409)
    if job["stage"] not in CLOSABLE:
        raise AppError("stage_locked", "No se puede cerrar sin verificar una etapa que sigue activa o un informe ya completado. Consulta primero su estado.", 409)
    ledger, _ = backend.ledger()
    reservations = [r for r in ledger["reservations"] if r["id"].startswith(job_id + ":")]
    outcome = provider_outcome(job)
    job["unverified_closure"] = {
        "id": key, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner": owner, "reason": reason.strip(), "previous_stage": job["stage"],
        "provider_outcome": outcome, "verification": "not_verified",
        "reservation_retained_in_full": True,
        "retained_reservation_ids": [r["id"] for r in reservations],
        "accepted_possible_additional_charge": True, "inference_replayed": False,
        "new_analysis_requires_new_id": True}
    job["stage"] = "closed_unverified"
    if job.get("recovery"):
        job["recovery"] = dict(job["recovery"], blocked=False, administratively_closed=True,
                               provider_outcome=outcome)
    backend.save_job(job, etag)
    backend.release(job_id)
    return job
