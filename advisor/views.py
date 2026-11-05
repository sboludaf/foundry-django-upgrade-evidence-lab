import json
import os
from functools import wraps

from azure.core.exceptions import AzureError
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import pipeline
from .auth import COOKIE, login_owner, logout_owner, owner_required
from .errors import AppError
from .repository import store


def errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except AppError as exc:
            response = JsonResponse({"code": exc.code, "error": str(exc)}, status=exc.status)
        except AzureError:
            response = JsonResponse({"code": "azure_unavailable", "error": "Azure no confirmó la operación. Revisa su estado antes de repetir."}, status=503)
        response["Cache-Control"] = "no-store"
        response["X-Frame-Options"] = "DENY"
        return response
    return wrapped


def body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError) as exc:
        raise AppError("invalid_input", "El cuerpo debe ser un objeto JSON válido.", 400) from exc
    if not isinstance(data, dict):
        raise AppError("invalid_input", "El cuerpo debe ser un objeto JSON.", 400)
    return data


@require_GET
def health(request):
    return JsonResponse({"status": "ok", "commit": os.getenv("APP_COMMIT", "development"), "storage_configured": bool(os.getenv("AZURE_STORAGE_ACCOUNT")),
                         "grounding_configured": bool(os.getenv("BING_CONNECTION_ID"))})


@errors
@require_http_methods(["GET", "POST"])
@ensure_csrf_cookie
def login(request):
    if request.method == "GET":
        return render(request, "advisor/login.html")
    token = login_owner(request, request.POST.get("username", ""), request.POST.get("password", ""))
    rotate_token(request)
    response = redirect("/")
    response.set_cookie(COOKIE, token, secure=not settings.DEBUG,
                        httponly=True, samesite="Lax")
    return response


@errors
@require_POST
@owner_required
def logout(request):
    logout_owner(request)
    response = redirect("/login/")
    response.delete_cookie(COOKIE, samesite="Lax")
    return response


@errors
@owner_required
@ensure_csrf_cookie
@require_GET
def index(request):
    return render(request, "advisor/index.html", {"jobs": store().jobs(request.owner),
                                                  "grounding_ready": bool(os.getenv("BING_CONNECTION_ID"))})


@errors
@require_POST
@owner_required
def create_job(request):
    data = body(request)
    job = pipeline.create(request.owner, data.get("version"), data.get("idempotency_key"))
    return JsonResponse({"id": job["id"], "stage": job["stage"], "url": "/reports/" + job["id"] + "/"}, status=201)


@errors
@require_POST
@owner_required
def action(request, report_id, operation):
    job_id = str(report_id)
    if operation == "sources":
        job = pipeline.prepare(request.owner, job_id)
    elif operation == "ground":
        job = pipeline.ground(request.owner, job_id)
    elif operation == "reconcile":
        job = pipeline.reconcile(request.owner, job_id)
    elif operation == "review":
        data = body(request)
        job = pipeline.review(request.owner, job_id, data.get("evidence"), data.get("reason"), data.get("review_version"))
    elif operation == "decide":
        job = pipeline.decide(request.owner, job_id)
    elif operation == "close":
        job = pipeline.abandon(request.owner, job_id)
    else:
        raise AppError("not_found", "Operación desconocida.", 404)
    return JsonResponse(job)


@errors
@require_GET
@owner_required
def report_json(request, report_id):
    job, _ = store().job(str(report_id), request.owner)
    response = JsonResponse(job, json_dumps_params={"ensure_ascii": False})
    response["Content-Disposition"] = 'attachment; filename="django-' + str(report_id) + '.json"'
    return response


@errors
@require_GET
@owner_required
@ensure_csrf_cookie
def report_html(request, report_id):
    job, _ = store().job(str(report_id), request.owner)
    draft = job["reviewed"] or job.get("original_validated") or job.get("original")
    download = request.GET.get("download") == "1"
    response = render(request, "advisor/report.html", {"job": job, "draft": json.dumps(draft, ensure_ascii=False, indent=2),
                                                        "grounding_ready": bool(os.getenv("BING_CONNECTION_ID")),
                                                        "has_extraction": job.get("original") is not None,
                                                        "original_pretty": json.dumps(job.get("original"), ensure_ascii=False, indent=2),
                                                        "reviewed_pretty": json.dumps(job.get("reviewed"), ensure_ascii=False, indent=2),
                                                        "download": download,
                                                        "report_css": (settings.BASE_DIR / "static/advisor/styles.css").read_text() if download else ""})
    if download:
        response["Content-Disposition"] = 'attachment; filename="django-' + str(report_id) + '.html"'
    return response
