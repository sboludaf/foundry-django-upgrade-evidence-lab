"""Factual, conservative report text. Decision-1 values are retained separately."""
import copy
from .domain import compare, numbers
from .extraction import review_draft

STATES = {"documented_affected": "Afectación documentada", "documented_fix": "Corrección documentada",
          "documented_not_applicable": "Condición de uso descartada por el revisor", "unresolved": "Aplicabilidad sin resolver"}


def conclusion(job):
    comparison = job.get("comparison") or {"issues": [], "unresolved": []}
    reasons, pending, candidates = [], list(comparison["unresolved"]), []
    for issue in comparison["issues"]:
        reasons.append(issue["cve"] + ": " + STATES[issue["state"]] +
                       ("; corrección " + issue["fixed"] if issue.get("fixed") else "."))
        if issue["state"] == "documented_affected" and issue.get("fixed") and numbers(issue["fixed"]) > numbers(job["version"]):
            candidates.append(issue["fixed"])
    decision = job.get("decision")
    if not job.get("reviewed"):
        pending.append("Confirma o corrige todos los avisos antes de obtener una conclusión.")
    elif not decision:
        pending.append("Falta solicitar la decisión sobre esta revisión.")
    elif job.get("decision_stale"):
        pending.append("La decisión anterior está obsoleta; recalcula explícitamente sobre la revisión actual.")
    else:
        pending.extend(decision.get("warnings", []))
        if decision["response"]["answers"]["reason"]["choice"] == "insufficient-evidence":
            pending.append("Decision-1 indica evidencia insuficiente.")
        if not decision.get("actionable", False):
            pending.append("La decisión no es accionable con los datos actuales.")
    pending = sorted(set(pending))
    affected = any(i["state"] == "documented_affected" for i in comparison["issues"])
    unsupported = (job.get("reviewed") or {}).get("support", {}).get("supported") is False
    if pending:
        label, code = "Evidencia insuficiente", "insufficient"
    elif affected or unsupported:
        label, code = "Actualización indicada", "indicated"
    else:
        label, code = "No indicada con la evidencia disponible", "not_indicated"
    proposed = max(candidates, key=numbers) if candidates else None
    return {"label": label, "code": code, "installed_version": job["version"], "proposed_version": proposed,
            "proposal_scope": "Candidata de la misma rama; su conveniencia depende de resolver las condiciones pendientes.",
            "reasons": reasons, "pending": pending,
            "scope": "Sólo los tres avisos y el lote fechado de este informe. No acredita ausencia de otras vulnerabilidades ni compatibilidad de tu aplicación."}


def report_payload(job):
    value = copy.deepcopy(job)
    value["conclusion"] = conclusion(job)
    if value.get("comparison"):
        for issue in value["comparison"]["issues"]:
            issue["state_label"] = STATES[issue["state"]]
    if job.get("original") is not None:
        value["review_proposal"] = review_draft(job)
        value["proposal_comparison"] = compare(job["version"], value["review_proposal"])
    return value
