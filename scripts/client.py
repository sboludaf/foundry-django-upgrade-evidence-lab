"""Documented user API client: credentials and cookies remain only in memory."""
import argparse
import getpass
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from html.parser import HTMLParser


def load_review(path):
    filename = Path(path).expanduser()
    if filename.stat().st_size > 65536:
        raise ValueError("El archivo de revisión supera 64 KiB.")
    try:
        value = json.loads(filename.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("No se pudo leer un objeto JSON UTF-8 de revisión.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("issues"), list):
        raise ValueError("El archivo debe contener sólo la evidencia (summary, issues, support, uncertainty).")
    return value


class CSRF(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "csrfmiddlewaretoken":
            self.token = attrs.get("value")


class Client:
    def __init__(self, url):
        parsed = urllib.parse.urlsplit(url)
        local = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
        if (parsed.scheme != "https" and not local) or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("Usa la URL HTTPS limpia de tu propia aplicación.")
        self.url = url.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        origin = (parsed.scheme, parsed.hostname, parsed.port)
        class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                target = urllib.parse.urlsplit(urllib.parse.urljoin(request.full_url, newurl))
                if (target.scheme, target.hostname, target.port) != origin:
                    raise RuntimeError("La aplicación intentó redirigir fuera de su origen.")
                return super().redirect_request(request, fp, code, msg, headers, newurl)
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies), SameOriginRedirect())
        self.token = None

    def request(self, path, data=None, form=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Ruta local no válida.")
        headers = {"Accept": "application/json", "Referer": self.url + "/"}
        body = None
        if data is not None:
            headers["X-CSRFToken"] = self.token
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        req = urllib.request.Request(self.url + path, data=body, headers=headers)
        try:
            with self.http.open(req, timeout=90) as response:
                content = response.read(100000)
                if response.headers.get_content_type() == "application/json":
                    return json.loads(content)
                parser = CSRF()
                parser.feed(content.decode())
                if parser.token:
                    self.token = parser.token
                return content.decode()
        except urllib.error.HTTPError as exc:
            # Never echo request bodies, cookies or authorization.
            recovery = {400: "Corrige la entrada antes de enviarla.", 401: "Inicia una sesión nueva.",
                        403: "Inicia una sesión nueva.", 409: "Consulta show y reconcilia la etapa existente.",
                        422: "Corrige los campos indicados; no repitas grounding.",
                        413: "Reduce el texto conservando citas y datos críticos."}.get(exc.code, "Consulta show antes de repetir la operación.")
            codes = {"invalid_input", "invalid_evidence", "unverified_claim", "unverified_branch", "unverified_fix",
                     "unverified_range", "wrong_branch", "unverified_condition", "unverified_support", "review_reason",
                     "review_required", "concurrent_change", "stage_locked", "environment_evidence", "decision_input_limit"}
            detail = ""
            try:
                error = json.loads(exc.read(8192))
                if error.get("code") in codes and isinstance(error.get("error"), str):
                    detail = " [" + error["code"] + "] " + "".join(c for c in error["error"][:500] if c.isprintable())
            except (ValueError, AttributeError):
                pass
            raise RuntimeError("HTTP " + str(exc.code) + detail + " " + recovery) from None

    def login(self, username, password):
        self.request("/login/")
        return self.request("/login/", {"username": username, "password": password,
                                         "csrfmiddlewaretoken": self.token}, form=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    client = Client(args.url)
    client.login(input("Usuario propietario: "), getpass.getpass("Contraseña (entrada oculta): "))
    print("Sesión establecida. Nunca se escriben cookies o credenciales en disco.")
    job_id = None
    while True:
        command = input("Acción [prepare, show, ground, reconcile, review, decide, close, quit]: ").strip()
        if command == "quit":
            return
        try:
            if command == "prepare":
                key = str(uuid.uuid4())
                print("ID estable de solicitud:", key)
                value = client.request("/api/jobs/", {"version": input("Django X.Y.Z: "), "idempotency_key": key})
                job_id = value["id"]
                client.request("/api/jobs/" + job_id + "/sources/", {})
            else:
                if not job_id:
                    job_id = str(uuid.UUID(input("ID del informe: ")))
                if command == "show":
                    print(json.dumps(client.request("/api/reports/" + job_id + "/"), ensure_ascii=False, indent=2))
                elif command == "review":
                    print("En la web puedes revisar la tabla sin editar JSON. Para esta interfaz, guarda sólo la evidencia revisada en un archivo UTF-8; nunca credenciales.")
                    evidence = load_review(input("Ruta del archivo JSON revisado: "))
                    current = client.request("/api/reports/" + job_id + "/")
                    print(client.request("/api/jobs/" + job_id + "/review/", {
                        "evidence": evidence, "reason": input("Motivo de revisión: "),
                        "review_version": current["review_version"]})["stage"])
                elif command in ("ground", "reconcile", "decide", "close"):
                    print(client.request("/api/jobs/" + job_id + "/" + command + "/", {})["stage"])
                else:
                    print("Acción no reconocida.")
        except (OSError, ValueError, RuntimeError) as error:
            print(str(error))


if __name__ == "__main__":
    main()
