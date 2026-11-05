import hashlib
import secrets
import time
from functools import wraps

from django.contrib.auth.hashers import check_password
from django.http import JsonResponse
from django.shortcuts import redirect

from .errors import AppError
from .repository import store

COOKIE = "django_owner"


def owner_for(request):
    token = request.COOKIES.get(COOKIE, "")
    if len(token) < 32 or len(token) > 100:
        return None
    session, _ = store().get("sessions", hashlib.sha256(token.encode()).hexdigest())
    if not session or session["expires"] <= int(time.time()):
        return None
    owner, _ = store().get("config", "owner")
    if not owner or session.get("generation") != owner.get("generation") or session["owner"] != owner["username"]:
        return None
    return session["owner"]


def owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        request.owner = owner_for(request)
        if not request.owner:
            if request.path.startswith("/api/"):
                return JsonResponse({"code": "unauthorized", "error": "Inicia sesión como propietario."}, status=401)
            return redirect("/login/")
        return view(request, *args, **kwargs)
    return wrapped


def login_owner(request, username, password):
    backend = store()
    # Public endpoint: persist a bounded attempt counter, never the IP or password.
    ip = request.META.get("REMOTE_ADDR", "")
    key = hashlib.sha256((ip + ":" + str(int(time.time()) // 900)).encode()).hexdigest()
    limit, etag = backend.get("login", key)
    limit = limit or {"attempts": 0}
    if limit["attempts"] >= 5:
        raise AppError("login_rate", "Espera quince minutos antes de volver a iniciar sesión.", 429)
    limit["attempts"] += 1
    backend.put("login", key, limit, etag)
    owner, _ = backend.get("config", "owner")
    if not owner:
        raise AppError("needs_configuration", "El operador debe inicializar el propietario por entrada protegida.", 503)
    if not isinstance(password, str) or len(password) > 256:
        raise AppError("unauthorized", "Credenciales no válidas.", 401)
    valid = check_password(password, owner["password_hash"])
    if not valid or not secrets.compare_digest(str(username).encode(), owner["username"].encode()):
        raise AppError("unauthorized", "Credenciales no válidas.", 401)
    token = secrets.token_urlsafe(32)
    backend.put("sessions", hashlib.sha256(token.encode()).hexdigest(),
                {"owner": owner["username"], "generation": owner["generation"], "expires": int(time.time()) + 3600})
    return token


def logout_owner(request):
    token = request.COOKIES.get(COOKIE, "")
    key = hashlib.sha256(token.encode()).hexdigest()
    session, etag = store().get("sessions", key)
    if session:
        store().remove("sessions", key, etag)
