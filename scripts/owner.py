"""Initialize/rotate only this environment's owner. Password never leaves stdin/memory."""
import getpass
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import load_public
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.contrib.auth.hashers import make_password
from advisor.repository import store


def configure(username, password):
    if not username or len(username) > 80 or len(password) < 16 or len(password) > 256:
        raise ValueError("Usuario de 1–80 caracteres y contraseña de 16–256 caracteres.")
    backend = store()
    _, etag = backend.get("config", "owner")
    backend.put("config", "owner", {"username": username, "password_hash": make_password(password),
                                   "generation": uuid.uuid4().hex}, etag)


if __name__ == "__main__":
    load_public()
    username = input("Usuario propietario: ").strip()
    first = getpass.getpass("Contraseña (mínimo 16 caracteres, entrada oculta): ")
    second = getpass.getpass("Repite la contraseña (entrada oculta): ")
    if first != second:
        raise SystemExit("No coinciden; no se guardó ningún cambio.")
    configure(username, first)
    print("Hash del propietario guardado en Azure Table. Sesiones anteriores revocadas.")
