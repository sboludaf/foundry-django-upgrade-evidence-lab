"""Explicit administrative closure; never a substitute for verified evidence."""
import hashlib
import json


CLOSABLE = {"created", "source_failed", "sources_ready", "awaiting_review", "reviewed",
            "grounding_failed", "decision_failed", "recovery_required", "uncertain"}
CLOSED = {"closed_unverified", "closed_unrecoverable"}


def state_token(job):
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def provider_outcome(job):
    if job.get("stage") in ("uncertain", "recovery_required"):
        return "unknown"
    snapshot = job.get("response_snapshot") or {}
    if snapshot.get("status") in ("completed", "failed", "incomplete"):
        return snapshot["status"]
    if (job.get("grounding") or {}).get("searches") or job.get("decision"):
        return "completed"
    return "unknown" if job.get("effect") or job.get("response_id") else "not_started"


def closure_view(job):
    closed = job.get("stage") in CLOSED
    recorded = job.get("unverified_closure") or job.get("operator_disposition") or {}
    outcome = recorded.get("provider_outcome", "unknown") if closed else provider_outcome(job)
    labels = {"unknown": "Sin confirmar", "not_started": "No iniciada",
              "completed": "Respuesta recibida; evidencia sin verificar",
              "failed": "Fallo informado por el proveedor", "incomplete": "Respuesta incompleta"}
    return {"closed": closed, "verified": False if closed else None, "actionable": False if closed else None,
            "can_close": job.get("stage") in CLOSABLE,
            "state_token": state_token(job),
            "provider_outcome": outcome, "provider_label": labels.get(outcome, "Sin confirmar"),
            "reason": recorded.get("reason", "Cierre administrativo sin resultado recuperable.") if closed else None,
            "new_analysis_requires_new_id": closed,
            "reservation_retained_in_full": bool(recorded.get("reservation_retained_in_full")) if closed else None}
