from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")
MAX_SOURCE_BYTES = 512_000
ALLOWED_SOURCE_HOSTS = {"docs.djangoproject.com", "www.djangoproject.com", "pypi.org"}
SOURCE_CATALOG = (
    {"kind": "security", "title": "Django security releases", "url": "https://docs.djangoproject.com/en/dev/releases/security/"},
    {"kind": "support", "title": "Django supported versions", "url": "https://www.djangoproject.com/download/"},
    {"kind": "package", "title": "Django package metadata", "url": "https://pypi.org/pypi/Django/json"},
)


class AdvisorError(Exception):
    """A user-facing, non-secret failure in the analysis pipeline."""


class InvalidInputError(AdvisorError):
    pass


class ConfigurationError(AdvisorError):
    pass


class IntegrationError(AdvisorError):
    pass


def validate_version(value: str) -> str:
    value = (value or "").strip()
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise InvalidInputError("Escribe una versión Django final con formato X.Y.Z.")
    if int(match.group("major")) < 1:
        raise InvalidInputError("La versión Django no es válida.")
    return value


def _source_url_is_allowed(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_SOURCE_HOSTS and not parsed.username and not parsed.password


def fetch_source(url: str) -> dict[str, Any]:
    if not _source_url_is_allowed(url):
        raise IntegrationError("La fuente está fuera de la allowlist oficial.")
    request = urllib.request.Request(url, headers={"User-Agent": "django-upgrade-evidence/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            final_url = response.geturl()
            if final_url != url and not _source_url_is_allowed(final_url):
                raise IntegrationError("La fuente redirigió fuera de la allowlist oficial.")
            body = response.read(MAX_SOURCE_BYTES + 1)
            if len(body) > MAX_SOURCE_BYTES:
                raise IntegrationError("La fuente oficial supera el límite de tamaño.")
            return {
                "url": final_url,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "content_type": response.headers.get("content-type", ""),
            }
    except (urllib.error.URLError, TimeoutError) as exc:
        raise IntegrationError("No se pudo recuperar una fuente oficial; inténtalo de nuevo tras revisar la red.") from exc


def collect_evidence() -> list[dict[str, Any]]:
    """Retrieve metadata only; raw source bodies never enter the report."""
    return [fetch_source(source["url"]) | {"kind": source["kind"], "title": source["title"]} for source in SOURCE_CATALOG]


class FoundryUpgradeAdvisor:
    def __init__(self) -> None:
        self.foundry_endpoint = os.getenv("AZURE_FOUNDRY_ENDPOINT", "").rstrip("/")
        self.decision_endpoint = os.getenv("AZURE_DECISION_ENDPOINT", "").rstrip("/")
        self.deployment = os.getenv("AZURE_DECISION_DEPLOYMENT", "")

    @property
    def configured(self) -> bool:
        return bool(self.foundry_endpoint and self.decision_endpoint and self.deployment)

    def _access_token(self) -> str:
        try:
            from azure.identity import AzureCliCredential, ManagedIdentityCredential
        except ImportError as exc:
            raise ConfigurationError("Instala el extra Azure Identity para activar el análisis real.") from exc
        mode = os.getenv("AZURE_CREDENTIAL_MODE", "cli").lower()
        credential = ManagedIdentityCredential() if mode == "managed_identity" else AzureCliCredential(tenant_id=os.getenv("AZURE_TENANT_ID") or None)
        try:
            return credential.get_token("https://ai.azure.com/.default").token
        except Exception as exc:
            raise IntegrationError("No se pudo obtener la identidad Entra configurada.") from exc

    def _call_decision_model(self, version: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        token = self._access_token()
        payload = json.dumps({
            "package": "Django",
            "version": version,
            "evidence": evidence,
            "response_schema": {
                "upgrade_needed": "noul",
                "reason": "choice",
                "urgency": "score",
            },
        }).encode("utf-8")
        request = urllib.request.Request(
            self.decision_endpoint,
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "django-upgrade-evidence/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status not in {200, 201, 202}:
                    raise IntegrationError("Microsoft Decision-1 no aceptó la solicitud.")
                data = json.loads(response.read(MAX_SOURCE_BYTES))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise IntegrationError("La llamada a Microsoft Decision-1 no produjo una respuesta utilizable.") from exc
        return {"deployment": self.deployment, "response": data}

    def analyze(self, version: str) -> dict[str, Any]:
        version = validate_version(version)
        if not self.configured:
            raise ConfigurationError("Azure no está configurado. Añade el endpoint, deployment y Decision-1 antes de analizar.")
        evidence = collect_evidence()
        decision = self._call_decision_model(version, evidence)
        return {
            "package": "Django",
            "version": version,
            "status": "complete",
            "evidence": evidence,
            "decision": decision,
            "uncertainty": [],
            "review": {"status": "pending", "note": "Revisa las fuentes y confirma el informe antes de aplicar cambios."},
        }
