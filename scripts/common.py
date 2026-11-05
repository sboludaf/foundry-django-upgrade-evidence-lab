"""Read only public deployment outputs from the caller's own azd environment."""
import json
import os
import subprocess

PUBLIC = ("AZURE_STORAGE_ACCOUNT", "AZURE_AI_PROJECT_ENDPOINT", "AZURE_AI_ENDPOINT",
          "AZURE_AI_MODEL_DEPLOYMENT_NAME", "AZURE_DECISION_DEPLOYMENT",
          "BING_CONNECTION_ID", "BING_CUSTOM_SEARCH_INSTANCE_NAME", "AZURE_TENANT_ID",
          "SERVICE_WEB_ENDPOINT_URL", "LAB_EXPIRES_AT")


def load_public():
    env = dict(os.environ, AZURE_DEV_USER_AGENT="microsoft_foundry_skill")
    result = subprocess.run(["azd", "env", "get-values", "--output", "json"], env=env,
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("No se pudo leer el entorno azd seleccionado; no se muestran sus valores.")
    values = json.loads(result.stdout)
    for name in PUBLIC:
        if values.get(name):
            os.environ.setdefault(name, str(values[name]))
    return {name: values.get(name) for name in PUBLIC}
