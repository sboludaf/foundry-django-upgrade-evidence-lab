import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from datetime import datetime, timezone

from .errors import AppError

HOSTS = {"docs.djangoproject.com", "www.djangoproject.com", "pypi.org"}
MAX_BYTES = 512_000


def allowed(url):
    try:
        p = urllib.parse.urlsplit(url)
        return p.scheme == "https" and p.hostname in HOSTS and p.port in (None, 443) and not p.username and not p.password
    except ValueError:
        return False


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        previous, target = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if not allowed(newurl) or (previous.hostname, previous.port) != (target.hostname, target.port):
            raise AppError("source_redirect", "La fuente intentó redirigir fuera de su origen.", 502)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.ignored = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if tag in ("p", "li", "h1", "h2", "h3", "section", "tr", "td", "dt", "dd", "div"):
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)
    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def normalized(text):
    return re.sub(r"\s+", " ", text).strip()


def fetch(url):
    if not allowed(url):
        raise AppError("source_not_allowed", "La fuente debe ser oficial Django o PyPI.", 400)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DjangoUpgradeEvidence/0.2"})
        with urllib.request.build_opener(OfficialRedirect()).open(req, timeout=12) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise AppError("source_size", "La fuente supera 512 KiB.", 502)
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/json", "text/plain"):
                raise AppError("source_type", "La fuente no devuelve un formato admitido.", 502)
            raw = body.decode("utf-8")
            if content_type == "text/html":
                parser = Text()
                parser.feed(raw)
                text = normalized("".join(parser.parts))
            else:
                text = normalized(raw)
            return {"url": response.geturl(), "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                    "content_type": content_type, "text": text}, body
    except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
        raise AppError("source_failure", "No se pudo recuperar una fuente oficial. No se llamó a los modelos.", 502) from exc


def collect(version, backend, job_id):
    urls = [
        "https://docs.djangoproject.com/en/dev/releases/security/",
        "https://www.djangoproject.com/download/",
        "https://www.djangoproject.com/weblog/2026/nov/03/security-releases/",
        "https://pypi.org/pypi/Django/" + version + "/json",
        "https://docs.djangoproject.com/en/dev/releases/5.2.19/",
        "https://docs.djangoproject.com/en/dev/releases/6.0.10/",
    ]
    docs = []
    for url in urls:
        doc, body = fetch(url)
        name = "sources/" + doc["sha256"]
        try:
            backend.blob(name, body, doc["content_type"])
        except Exception as exc:
            from azure.core.exceptions import ResourceExistsError
            if not isinstance(exc, ResourceExistsError):
                raise
        doc["blob"] = name
        doc["cves"] = sorted(set("CVE-" + number for number in re.findall(r"CVE[- ]([0-9]{4}-[0-9]{4,10})", doc["text"])))
        # Reports retain bounded verified excerpts, not entire source pages.
        text = doc.pop("text")
        fragments = []
        if "pypi.org" in url:
            info = json.loads(body)["info"]
            fragments = ["Django " + info["version"] + "; requires_python: " + str(info.get("requires_python"))]
        elif "/download/" in url:
            target = ".".join(version.split(".")[:2])
            starts = [m.start() for m in re.finditer(re.escape(target), text)][:3]
            fragments = [text[max(0, i - 150):i + 400] for i in starts] or [text[:500]]
        else:
            if "/releases/5.2.19/" in url or "/releases/6.0.10/" in url:
                fixed = "5.2.19" if "5.2.19" in url else "6.0.10"
                fragments.append(release_intro(text, fixed))
            for cve in ("2026-108147", "2026-113733", "2026-114582"):
                start = text.find(cve)
                if start >= 0:
                    fragments.append(text[max(0, start - 8):start + 500])
            gdal = text.find("GDAL 3.")
            if gdal >= 0:
                fragments.append(text[max(0, gdal - 120):gdal + 350])
            if not fragments:
                fragments = [text[:500]]
        doc["fragments"] = fragments
        doc["excerpt"] = "\n[…]\n".join(fragments)
        docs.append(doc)
    return docs


def release_intro(text, fixed):
    # Page titles/navigation repeat the release name before the actual article.
    start = text.find("Django " + fixed + " fixes")
    if start < 0:
        raise AppError("source_layout_changed", "No se encontró el párrafo oficial de corrección; no se inventará.", 502)
    return text[max(0, start - 60):start + 350]


def excerpt_is_verified(quote, url, docs):
    if not isinstance(quote, str) or len(quote) > 700:
        return False
    return any(d["url"] == url and any(normalized(quote) in normalized(fragment)
               for fragment in d.get("fragments", [d["excerpt"]]))
               and len(normalized(quote)) >= 25 for d in docs)
