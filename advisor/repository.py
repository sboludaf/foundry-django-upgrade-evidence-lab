"""Azure-only persistence. No process-memory or local-disk product store."""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError, ResourceModifiedError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .errors import AppError


def charged_usage(ledger):
    """Keep cumulative counters intact; subtract only itemized terminal settlements.

    Settlements are operator reconciliation records, never accepted from a web
    request. They do not settle Azure billing or permit replay of an old effect.
    """
    reservations = {r["id"]: r for r in ledger["reservations"]}
    seen, released = set(), {}
    for entry in ledger.get("settlements", []):
        key, parent = entry.get("id"), entry.get("reservation_id")
        if not key or key in seen or parent not in reservations or not entry.get("proof"):
            raise AppError("ledger_invalid", "Conciliación del ledger no verificable; no se iniciará consumo.", 409)
        seen.add(key)
        totals = released.setdefault(parent, {"models": 0, "searches": 0})
        for field in ("models", "searches"):
            amount = entry.get("unused_" + field)
            if type(amount) is not int or amount < 0:
                raise AppError("ledger_invalid", "Conciliación del ledger no válida.", 409)
            totals[field] += amount
            if totals[field] > reservations[parent][field]:
                raise AppError("ledger_invalid", "La conciliación supera su reserva original.", 409)
    models = ledger["model_calls"] - sum(v["models"] for v in released.values())
    searches = ledger["searches"] - sum(v["searches"] for v in released.values())
    if min(models, searches) < 0:
        raise AppError("ledger_invalid", "El ledger no conserva sus contadores acumulados.", 409)
    return {"models": models, "searches": searches}


@lru_cache
def credential():
    if os.getenv("AZURE_CREDENTIAL_MODE") == "managed_identity":
        return ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID") or None)
    return AzureCliCredential(tenant_id=os.getenv("AZURE_TENANT_ID") or None)


class AzureStore:
    def __init__(self):
        account = os.getenv("AZURE_STORAGE_ACCOUNT")
        if not account:
            raise AppError("needs_configuration", "Configura el almacenamiento Azure del entorno.", 503)
        auth = credential()
        self.table = TableServiceClient(
            "https://" + account + ".table.core.windows.net", credential=auth, retry_total=0
        ).get_table_client("mvpstate")
        self.blobs = BlobServiceClient(
            "https://" + account + ".blob.core.windows.net", credential=auth, retry_total=0
        ).get_container_client("evidence")

    def get(self, partition, key):
        try:
            entity = self.table.get_entity(partition_key=partition, row_key=key)
        except ResourceNotFoundError:
            return None, None
        value = json.loads(entity["data"])
        if partition == "jobs" and "_blob" in value:
            value = json.loads(self.blobs.download_blob(value["_blob"], max_concurrency=1).readall())
        return value, entity.metadata["etag"]

    def put(self, partition, key, data, etag=None):
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-16-le")) > 60_000:
            raise AppError("state_limit", "La evidencia supera el tamaño permitido.", 413)
        entity = {"PartitionKey": partition, "RowKey": key, "data": encoded}
        try:
            if etag is None:
                self.table.create_entity(entity)
            else:
                self.table.update_entity(entity, mode=UpdateMode.REPLACE, etag=etag,
                                         match_condition=MatchConditions.IfNotModified)
        except (ResourceExistsError, ResourceModifiedError) as exc:
            raise AppError("concurrent_change", "El estado ha cambiado. Recarga antes de continuar.") from exc

    def remove(self, partition, key, etag):
        self.table.delete_entity(partition_key=partition, row_key=key, etag=etag,
                                 match_condition=MatchConditions.IfNotModified)

    def blob(self, name, body, content_type="application/json"):
        self.blobs.upload_blob(name=name, data=body, overwrite=False,
                               content_settings=ContentSettings(content_type=content_type))

    def save_job(self, job, etag):
        job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = json.dumps(job, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 200_000:
            raise AppError("state_limit", "El informe supera el límite de 200 KiB.", 413)
        name = "jobs/" + job["id"] + "/" + uuid.uuid4().hex + ".json"
        self.blob(name, body)
        metadata = {key: job.get(key) for key in ("id", "owner", "version", "stage", "updated_at")}
        self.put("jobs", job["id"], dict(metadata, _blob=name), etag)

    def job(self, job_id, owner):
        job, etag = self.get("jobs", job_id)
        if not job or job["owner"] != owner:
            raise AppError("not_found", "Informe no encontrado.", 404)
        return job, etag

    def jobs(self, owner):
        result = []
        for entity in self.table.query_entities("PartitionKey eq @p", parameters={"p": "jobs"}, results_per_page=30):
            job = json.loads(entity["data"])
            if job["owner"] == owner:
                result.append({k: job.get(k) for k in ("id", "version", "stage", "updated_at")})
        return sorted(result, key=lambda row: row.get("updated_at", ""), reverse=True)[:30]

    def ledger(self):
        value, etag = self.get("control", "ledger")
        if value is None:
            value = {"model_calls": 0, "searches": 0, "active_job": None, "reservations": []}
        return value, etag

    def acquire(self, job_id):
        value, etag = self.ledger()
        if value["active_job"] not in (None, job_id):
            raise AppError("analysis_active", "Ya existe un análisis activo. Revisa su estado antes de iniciar otro.")
        value["active_job"] = job_id
        self.put("control", "ledger", value, etag)

    def reserve(self, job_id, step_id, models, searches):
        deadline = os.getenv("LAB_EXPIRES_AT", "2026-11-09T11:00:00Z")
        try:
            expires = datetime.fromisoformat(deadline.replace("Z", "+00:00")).timestamp()
        except ValueError as exc:
            raise AppError("needs_configuration", "La caducidad de consumo no es válida.", 503) from exc
        if time.time() >= expires:
            raise AppError("cycle_expired", "La ventana de consumo ha terminado.", 403)
        value, etag = self.ledger()
        if value["active_job"] != job_id:
            raise AppError("analysis_active", "El análisis no conserva el bloqueo de ejecución.")
        if any(r["id"] == step_id for r in value["reservations"]):
            raise AppError("effect_already_reserved", "La etapa ya fue reservada. Reconcilia su ID; no se reenviará.")
        charged = charged_usage(value)
        if charged["models"] + models > int(os.getenv("MODEL_CALL_LIMIT", "12")) or charged["searches"] + searches > int(os.getenv("BING_QUERY_LIMIT", "6")):
            raise AppError("budget_exhausted", "Se ha alcanzado el límite de consumo del entorno.", 429)
        value["model_calls"] += models
        value["searches"] += searches
        value["reservations"].append({"id": step_id, "models": models, "searches": searches, "at": int(time.time())})
        self.put("control", "ledger", value, etag)

    def release(self, job_id):
        value, etag = self.ledger()
        if value["active_job"] == job_id:
            value["active_job"] = None
            self.put("control", "ledger", value, etag)


@lru_cache
def store():
    return AzureStore()
