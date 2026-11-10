import copy
import re
from .errors import AppError
from .sources import excerpt_is_verified

VERSION = re.compile(r"^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,3})$")


def version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value) or value.startswith("0."):
        raise AppError("invalid_input", "Escribe una versión final Django X.Y.Z.", 400)
    return value


def numbers(value):
    return tuple(int(x) for x in version(value).split("."))


def validate_evidence(evidence, docs):
    if not isinstance(evidence, dict) or not isinstance(evidence.get("issues"), list) or len(evidence["issues"]) > 5:
        raise AppError("invalid_evidence", "La extracción no tiene el formato esperado.", 422)
    result = copy.deepcopy(evidence)
    if not isinstance(result.get("summary"), str) or len(result["summary"]) > 1500:
        raise AppError("invalid_evidence", "El resumen no es válido.", 422)
    cves = [i.get("cve") for i in result["issues"] if isinstance(i, dict)]
    if any(not isinstance(cve, str) for cve in cves) or len(cves) != len(set(cves)):
        raise AppError("invalid_evidence", "Un aviso no puede repetirse.", 422)
    for issue in result["issues"]:
        if not isinstance(issue, dict):
            raise AppError("invalid_evidence", "Cada aviso debe ser un objeto.", 422)
        if not re.fullmatch(r"CVE-[0-9]{4}-[0-9]{4,10}", str(issue.get("cve", ""))):
            raise AppError("invalid_evidence", "Identificador CVE no válido.", 422)
        if not isinstance(issue.get("branches"), list) or len(issue["branches"]) > 5:
            raise AppError("invalid_evidence", "Faltan las ramas documentadas.", 422)
        names = [str(b.get("branch")) for b in issue["branches"] if isinstance(b, dict)]
        if len(names) != len(set(names)):
            raise AppError("invalid_evidence", "Un aviso no puede repetir una rama.", 422)
        for branch in issue["branches"]:
            if not isinstance(branch, dict):
                raise AppError("invalid_evidence", "Cada rama debe ser un objeto.", 422)
            name = branch.get("branch", "")
            if not isinstance(name, str) or not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}", name):
                raise AppError("invalid_evidence", "Rama no válida.", 422)
            if not excerpt_is_verified(branch.get("quote", ""), branch.get("source_url"), docs):
                raise AppError("unverified_claim", "Un campo crítico no tiene un fragmento exacto del lote oficial.", 422)
            for field in ("affected_from", "affected_to", "fixed"):
                if branch.get(field) is not None:
                    value = version(branch[field])
                    if ".".join(value.split(".")[:2]) != name:
                        raise AppError("wrong_branch", "La corrección y el rango deben pertenecer a su rama.", 422)
            low, high, fixed = (branch.get(k) for k in ("affected_from", "affected_to", "fixed"))
            quote = branch["quote"]
            source = next(d for d in docs if d["url"] == branch["source_url"])
            if fixed and ("/releases/" + fixed + "/") in branch["source_url"]:
                if issue["cve"] not in source.get("cves", []):
                    raise AppError("unverified_branch", "El CVE no aparece en las notas de esa corrección.", 422)
            elif any((low, high, fixed)) and issue["cve"] not in quote.replace("CVE ", "CVE-"):
                raise AppError("unverified_branch", "La cita debe relacionar este CVE con las versiones de su rama.", 422)
            if fixed and fixed not in quote:
                raise AppError("unverified_fix", "La versión corregida debe aparecer en el fragmento citado.", 422)
            before_fix = fixed and re.search(r"(?:before|prior to|antes de)\s+" + re.escape(fixed), quote, re.I)
            if high and high not in quote and not (before_fix and numbers(high) == (numbers(fixed)[0], numbers(fixed)[1], numbers(fixed)[2] - 1)):
                raise AppError("unverified_range", "El límite superior debe estar documentado o derivarse de before seguido de la corrección.", 422)
            if low and low not in quote and not (low == name + ".0" and re.search(re.escape(name) + r"\s+(?:before|prior to)", quote, re.I)):
                raise AppError("unverified_range", "El límite inferior no está documentado. Usa null.", 422)
            if low and high and numbers(low) > numbers(high):
                raise AppError("invalid_evidence", "Rango de versiones invertido.", 422)
            if fixed and high and numbers(high) >= numbers(fixed):
                raise AppError("invalid_evidence", "La versión corregida no está fuera del rango afectado.", 422)
        conditions = issue.setdefault("conditions", [])
        if not isinstance(conditions, list) or len(conditions) > 5:
            raise AppError("invalid_evidence", "Condiciones no válidas.", 422)
        for condition in conditions:
            if not isinstance(condition, dict):
                raise AppError("invalid_evidence", "Cada condición debe ser un objeto.", 422)
            if condition.get("state") not in ("unknown", "met", "not_met"):
                raise AppError("invalid_evidence", "Una condición debe indicar unknown, met o not_met.", 422)
            if condition["state"] != "unknown" and (not isinstance(condition.get("observation"), str) or not 8 <= len(condition["observation"].strip()) <= 500):
                raise AppError("environment_evidence", "Describe cómo comprobaste la condición en tu entorno (8–500 caracteres).", 422)
            if not isinstance(condition.get("detail"), str) or len(condition["detail"]) > 500:
                raise AppError("invalid_evidence", "Describe la condición pendiente.", 422)
            if not excerpt_is_verified(condition.get("quote"), condition.get("source_url"), docs):
                raise AppError("unverified_condition", "Las condiciones críticas requieren una cita del lote oficial.", 422)
    support = result.get("support", {})
    if not isinstance(support, dict) or not (support.get("supported") is None or type(support.get("supported")) is bool):
        raise AppError("invalid_evidence", "Soporte no válido.", 422)
    if support.get("supported") is not None and not excerpt_is_verified(support.get("quote", ""), support.get("source_url"), docs):
        raise AppError("unverified_support", "El soporte necesita un fragmento oficial.", 422)
    uncertainty = result.setdefault("uncertainty", [])
    if not isinstance(uncertainty, list) or any(not isinstance(s, str) or len(s) > 500 for s in uncertainty) or len(uncertainty) > 8:
        raise AppError("invalid_evidence", "Incertidumbre no válida.", 422)
    return result


def compare(installed, evidence):
    current, branch = numbers(installed), ".".join(installed.split(".")[:2])
    result, unresolved = [], list(evidence.get("uncertainty", []))
    for issue in evidence["issues"]:
        found = next((b for b in issue["branches"] if b["branch"] == branch), None)
        state = "unresolved"
        if found:
            fixed = found.get("fixed")
            low, high = found.get("affected_from"), found.get("affected_to")
            if fixed and current >= numbers(fixed):
                state = "documented_fix"
            elif low and high and numbers(low) <= current <= numbers(high):
                state = "documented_affected"
        if state == "documented_affected" and any(c["state"] == "not_met" for c in issue.get("conditions", [])):
            state = "documented_not_applicable"
        if state == "unresolved":
            unresolved.append(issue["cve"] + ": aplicabilidad no resuelta para la rama " + branch)
        for condition in issue.get("conditions", []):
            if condition["state"] == "unknown":
                unresolved.append(issue["cve"] + ": " + condition["detail"])
        result.append({"cve": issue["cve"], "branch": branch, "state": state,
                       "fixed": found.get("fixed") if found else None,
                       "source_url": found.get("source_url") if found else None,
                       "conditions": issue.get("conditions", [])})
    if evidence.get("support", {}).get("supported") is None:
        unresolved.append("Soporte vigente no acreditado para la versión.")
    if not evidence["issues"]:
        unresolved.append("La ausencia de avisos no acredita una versión segura.")
    return {"issues": result, "unresolved": sorted(set(unresolved)),
            "actionable": not unresolved, "version": installed,
            "supported": evidence.get("support", {}).get("supported")}


def decision_warnings(comparison, response):
    reason = response["answers"]["reason"]["choice"]
    warnings = []
    if any(i["state"] == "documented_affected" for i in comparison["issues"]) and reason == "no-upgrade-needed":
        warnings.append("La opción del modelo contradice una afectación documentada por el comparador.")
    if comparison["unresolved"] and reason != "insufficient-evidence":
        warnings.append("La opción del modelo no conserva todas las condiciones críticas sin resolver.")
    if reason == "security-fix" and not any(i["state"] == "documented_affected" for i in comparison["issues"]):
        warnings.append("El modelo propone una corrección de seguridad sin afectación documentada aplicable.")
    if comparison.get("supported") is False and reason == "no-upgrade-needed":
        warnings.append("El modelo omite el fin de soporte documentado.")
    if comparison.get("supported") is True and reason == "unsupported":
        warnings.append("El modelo contradice el soporte vigente documentado.")
    return warnings
