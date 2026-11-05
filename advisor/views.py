from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .services import AdvisorError, FoundryUpgradeAdvisor, validate_version


REPORTS: dict[str, dict] = {}


@require_GET
def index(request: HttpRequest) -> HttpResponse:
    return render(request, "advisor/index.html")


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "django-upgrade-evidence", "azure_configured": FoundryUpgradeAdvisor().configured})


@csrf_exempt
@require_http_methods(["POST"])
def analyze(request: HttpRequest) -> JsonResponse:
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "El cuerpo debe ser JSON válido."}, status=400)
    try:
        version = validate_version(body.get("version", ""))
        report = FoundryUpgradeAdvisor().analyze(version)
    except AdvisorError as exc:
        status = 503 if "configur" in str(exc).lower() or "instala" in str(exc).lower() else 502
        return JsonResponse({"status": "needs_configuration" if status == 503 else "integration_error", "error": str(exc)}, status=status)
    report_id = str(uuid.uuid4())
    REPORTS[report_id] = {"id": report_id, "created_at": datetime.now(timezone.utc).isoformat(), **report}
    return JsonResponse({"report_id": report_id, "status": report["status"], "report_url": f"/reports/{report_id}/", "json_url": f"/api/reports/{report_id}/"}, status=201)


@require_GET
def report_json(request: HttpRequest, report_id: uuid.UUID) -> JsonResponse:
    report = REPORTS.get(str(report_id))
    if report is None:
        return JsonResponse({"error": "Informe no encontrado en esta sesión."}, status=404)
    response = JsonResponse(report)
    response["Content-Disposition"] = f'attachment; filename="django-upgrade-{report_id}.json"'
    return response


@require_GET
def report_html(request: HttpRequest, report_id: uuid.UUID) -> HttpResponse:
    report = REPORTS.get(str(report_id))
    if report is None:
        return JsonResponse({"error": "Informe no encontrado en esta sesión."}, status=404)
    return render(request, "advisor/report.html", {"report": report})
