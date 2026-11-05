# Django Upgrade Evidence

**Revisión 2 bloqueada; no está lista para Tester.** Este repositorio privado conserva el prototipo Django y el aprovisionamiento Foundry intentado en Azure. Todavía no entrega el recorrido completo ni una aplicación cloud desplegada.

El 05/11/2026 se crearon realmente la cuenta/proyecto Foundry y los despliegues GPT-6 Luna y Microsoft-Decision-1. La creación de Grounding with Bing Custom Search devolvió `InternalServerError: Unexpected error.` con dos versiones de API ARM. Ambas operaciones terminaron en Failed y la lectura posterior de la cuenta Bing devolvió 404. No se ejecutó `azd deploy`, ninguna inferencia ni búsqueda. La causa interna del proveedor no está determinada; no se atribuye a cuota, permisos o elegibilidad sin evidencia.

## Preparación local reproducible

Requisitos: Git, Python 3.11+, uv, Azure CLI y Azure Developer CLI. Se utilizaron azd 1.34.2 y SDKs preview fijados por `uv.lock`. Instala desde este repositorio, sin archivos del autor:

```sh
uv sync --frozen
uv run python manage.py check
uv run python manage.py test
uv run python manage.py runserver 127.0.0.1:8000
```

Abre http://127.0.0.1:8000. Es un prototipo local: valida una versión final X.Y.Z y muestra un bloqueo explícito. `POST /api/analyze/` con `{"version":"5.2.18"}` devuelve 503 sin llamadas pagadas; una entrada como `5.2` devuelve 400. Añadir endpoints de Azure no habilita el adaptador antiguo. `/health/` comprueba el proceso y expone por separado `integration_status` y `ready_for_test:false`.

Django del servidor usa 5.2.19. El valor escrito por el usuario es la versión a estudiar, independiente de la versión del servidor. El fichero `.env.example` sólo documenta nombres; la aplicación no carga automáticamente archivos .env. Nunca escribas credenciales en .env ni en argumentos. Configuración pública mediante entorno; para futuras credenciales de arranque, entrada protegida y sólo hash persistido.

El prototipo todavía carece de autenticación de propietario, persistencia Azure de informes/ledger, revisión humana y comparador por rama. No se debe exponer a una red pública. El antiguo adaptador Decision-1 enviaba un contrato de ejemplo no verificado: se ha retirado su envío y cualquier posibilidad de presentar un resultado completo.

## Aprovisionamiento en Azure propio

Los nombres se derivan de entorno y grupo; no hay endpoints del autor en la configuración desplegable. Usa una suscripción propia y un grupo exclusivo, con presupuesto y permisos aplicables. El despliegue independiente de Tester deberá usar otro entorno/grupo, cuenta, modelos e identidades.

```sh
az login
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd auth login
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd env new <entorno-propio>
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd env set AZURE_SUBSCRIPTION_ID <suscripcion-propia>
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd env set AZURE_LOCATION westeurope
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd env set AZURE_RESOURCE_GROUP <grupo-exclusivo>
az ad signed-in-user show --query id --output tsv
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd env set AZURE_PRINCIPAL_ID <id-publico-del-operador>
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd provision --preview --no-prompt
AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd provision --no-prompt
```

Sustituye los marcadores antes de ejecutar; no son secretos. `AZURE_PRINCIPAL_ID` es el object ID del usuario actual, no una clave ni el ID de una aplicación. La infraestructura asigna Azure AI User sobre su propia cuenta; el operador también necesita permisos de aprovisionamiento y asignación RBAC en su ámbito. Confirma disponibilidad regional y cuota antes del efecto. No cambies la suscripción para eludir un fallo.

`infra/main.bicep` crea el grupo; `infra/core.bicep` declara AIServices, el proyecto, ambos modelos y Bing Custom Search con su conexión. Serializa Luna → Decision-1 → proyecto para evitar conflictos simultáneos sobre la cuenta. Los modelos son Luna `2026-09-22` DataZoneStandard capacidad 10 y Decision-1 `1` DataZoneStandard capacidad 500. Deshabilita claves locales de AIServices. La clave Bing sólo se enlaza dentro de ARM a la conexión, sin outputs, ficheros o argumentos.

**La provisión completa aún falla en Bing.** La API actual del fichero es 2025-05-01-preview; el intento previo con 2020-06-10 también falló. `docs/provisioning-evidence.json` conserva IDs y estados saneados. Una preview correcta no acredita disponibilidad real. La instancia `django-official` y sus dominios todavía no existen; la conexión depende del recurso fallido. No se sustituye por Bing estándar o recuperación HTTP.

La secuencia del producto seguirá siendo `azd provision` y después `azd deploy`. **Este commit no permite completar el segundo paso:** ACR, Storage, Container Apps, RBAC de runtime y configuración de arranque todavía no están implementados; `azure.yaml` sólo declara el servicio futuro. No ejecutes `azd deploy` hasta completar esa infraestructura, el control de acceso y la integración. El Dockerfile usa Gunicorn pero aún no acredita un paquete cloud. No se ha comprobado `azd up`.

Ante un timeout de provisión, lee el deployment ARM y los IDs antes de repetir. En esta ejecución se conciliaron ambos fallos antes del segundo intento; no hay una operación pendiente conocida. Conserva el mismo entorno, grupo y recursos al reanudar. Un reintento sin cambio o diagnóstico adicional no resuelve el error del proveedor.

## Arquitectura acreditada

![Recursos realmente creados y pendientes](docs/architecture.svg)

Se han comprobado por GET ARM: AIServices, proyecto, dos modelos y rol de datos del operador. Estar aprovisionado no acredita invocación de ninguno de los modelos ni compatibilidad de Luna con Bing. No hay agente, conversación, búsqueda, instancia Bing, informe real ni hosting web.

`docs/architecture.md` distingue estos recursos de la arquitectura objetivo. No se hereda QA de la revisión 1 ni se denomina integración a los tests locales.

## Datos y adaptación del tutorial

Referencia: [tutorial Microsoft](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/build-a-grounded-package-upgrade-advisor-with-microsoft-decision-1/4568374). Se inspeccionó su [repositorio de ejemplo](https://github.com/SonakshiA/software-upgrade-advisor): sin licencia identificada, no se copió su código. La implementación es independiente. La propuesta seleccionó GPT-6 Luna como adaptación y conserva Decision-1 y Bing Custom Search; la combinación funcional sigue sin comprobar.

Fuentes previstas: [archivo de seguridad Django](https://docs.djangoproject.com/en/dev/releases/security/), [soporte](https://www.djangoproject.com/download/), notas por rama y PyPI. Sólo se admitirá evidencia oficial auténtica con URL, fecha, hash y fragmentos verificables. Los helpers actuales sólo conservan metadatos y no acreditan extracción. Antes de publicar extractos del repositorio Django debe conservarse su licencia BSD. No se distribuyen cuerpos web ni un gold inventado.

Decision-1 requiere su [API específica systemone](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) y respuestas tipadas noul/choice/score, todavía pendientes de implementar y validar. Las [herramientas Bing](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/bing-tools) exigen recurso, instancia y conexión; las citas de páginas y consultas deben conservarse según sus términos. Recuperar HTTP directo no reemplaza esa etapa.

## Costes y retirada

El ciclo privado autorizado de este laboratorio tiene estimación conjunta 5,45 EUR y techo 8 EUR; el run Implementer reserva 3,20 EUR, no es gasto facturado. Límites conjuntos: 40 invocaciones (incluidas internas), 24 búsquedas, 6.000 tokens entrada/1.000 salida total por invocación, dos entornos aislados y hasta dos correcciones. Esta ejecución ha consumido **0 inferencias, 0 búsquedas, 0 builds y 0 horas de réplica web**. No se declara factura cero: quedan recursos Azure persistentes y la reserva no se liquida.

Retirada prevista del grupo propio: 09/11/2026 10:00 Europe/Madrid. En un despliegue propio registra tu inventario y fecha. Revisa `docs/provisioning-evidence.json` y los recursos antes de eliminar; sólo el grupo exclusivo del entorno. Con autorización de retirada aplicable, el comando azd es `AZURE_DEV_USER_AGENT=microsoft_foundry_skill azd down`; no se ha ejecutado ni probado aquí. No borres GitHub, otros grupos o recursos compartidos.

Para continuar: resolver o diagnosticar la creación Bing con el proveedor; comprobar estado por lectura; completar instancia/conexión, agente real y límites; implementar autenticación, ledger, evidencia/revisión/decisión e informes; añadir hosting/almacenamiento, ejecutar provision/deploy y verificar el recorrido real. Sólo entonces entregar un SHA nuevo como ready_for_test.
