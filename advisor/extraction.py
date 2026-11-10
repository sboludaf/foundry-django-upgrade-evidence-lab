"""Source-backed field extraction, separate from the unmodified model response."""
import calendar
import copy
import re
from datetime import date

from .sources import normalized

TARGETS = ("CVE-2026-108147", "CVE-2026-113733", "CVE-2026-114582")
PATCH = r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4}"
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


def release_sentence(text, fixed):
    match = re.search(r"Django " + re.escape(fixed) + r" fixes\b.*?\bin (" + PATCH + r")\.(?=\s|$)", normalized(text))
    if not match or match[1].rsplit(".", 1)[0] != fixed.rsplit(".", 1)[0]:
        return None
    quote = match[0]
    return (quote, match[1]) if len(quote) <= 700 else None


def support_field(docs, branch):
    for doc in docs:
        if doc["url"] != "https://www.djangoproject.com/download/":
            continue
        for fragment in doc.get("fragments", [doc["excerpt"]]):
            if "End of extended support" not in fragment:
                continue
            pattern = (r"(?<![0-9.])" + re.escape(branch) + r"(?: LTS)?\s+" + PATCH + r"\s+" +
                       MONTH + r" (?:[0-9]{1,2}, )?[0-9]{4}\s+(" + MONTH + r") ([0-9]{4})(?![0-9])")
            row = re.search(pattern, fragment)
            if row:
                month = list(calendar.month_name).index(row[1])
                end = date(int(row[2]), month, calendar.monthrange(int(row[2]), month)[1])
                captured = date.fromisoformat(doc["retrieved_at"][:10])
                return {"supported": captured <= end, "source_url": doc["url"], "quote": row[0]}
    return {"supported": None, "source_url": None, "quote": ""}


def source_proposal(job):
    """No network, inference or invented bounds; review is always required."""
    docs = job["sources"]
    issues, missing = [], []
    starts = {
        TARGETS[0]: ("When using ", "Uso real de MultiPartParser y cargas base64 pendiente de comprobar."),
        TARGETS[1]: ("Admin users with the view ", "Modelo de usuario y permisos view/change pendientes de comprobar."),
        TARGETS[2]: ("Full protection requires GDAL ", "Versión real de GDAL pendiente de comprobar."),
    }
    for cve in TARGETS:
        branches, conditions = [], []
        for doc in docs:
            match = re.search(r"/releases/(" + PATCH + r")/$", doc["url"])
            if match and cve in doc.get("cves", []):
                for fragment in doc.get("fragments", [doc["excerpt"]]):
                    found = release_sentence(fragment, match[1])
                    if found:
                        quote, affected = found
                        branches.append({"branch": match[1].rsplit(".", 1)[0],
                            "affected_from": affected, "affected_to": affected, "fixed": match[1],
                            "source_url": doc["url"], "quote": quote})
                        break
            start, detail = starts[cve]
            if cve not in doc.get("cves", []):
                continue
            for fragment in doc.get("fragments", [doc["excerpt"]]):
                # Match a whole sentence. Dots in qualified Python names are not sentence ends.
                sentence = re.search(re.escape(start) + r".*?\.(?=\s+[A-Z]|$)", fragment)
                if sentence and len(sentence[0]) <= 700 and not conditions:
                    conditions.append({"detail": detail, "state": "unknown", "observation": "",
                                       "source_url": doc["url"], "quote": sentence[0]})
        if not conditions:
            missing.append(cve + ": no se capturó una frase completa con sus condiciones de uso.")
        issues.append({"cve": cve, "branches": branches, "conditions": conditions})
    return {"summary": "Versiones exactas nombradas por las notas oficiales; sin extrapolar rangos anteriores. Confirma cada aviso y las condiciones reales de tu entorno.",
            "issues": issues, "support": support_field(docs, job["version"].rsplit(".", 1)[0]),
            "uncertainty": missing}


def review_draft(job):
    if job.get("reviewed") is not None:
        return copy.deepcopy(job["reviewed"])
    return source_proposal(job)
