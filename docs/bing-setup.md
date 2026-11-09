# Bing Custom Search: configuración y diagnóstico

La plantilla usa Grounding with Bing **Custom** Search, SKU **G2** y API de cuenta **2020-06-10**. El SKU G1 pertenece a Grounding with Bing Search estándar. No intercambiar los dos servicios como recuperación de un fallo.

Fuente primaria contrastada: [ejemplo oficial Azure Samples, revisión c666ed8](https://github.com/Azure-Samples/foundry-bing-custom-search-arm/blob/c666ed8555bcb0d2a85fc71669bc08702d299dac/Project-folder/create-bing-resource.REST). Para la instancia se usa [customSearchConfigurations, API 2025-05-01-preview](https://github.com/Azure-Samples/foundry-bing-custom-search-arm/blob/c666ed8555bcb0d2a85fc71669bc08702d299dac/Project-folder/create-update-config.REST).

Con PROVISION_GROUNDING=true, Bicep prepara la cuenta G2, la configuración django-official con los dominios oficiales de Django y la ficha Django de PyPI, y después la conexión ApiKey del proyecto. La clave se resuelve dentro de ARM y no se devuelve como output, argumento o archivo local. La aplicación recibe sólo el ID de conexión y el nombre de instancia. PROVISION_GROUNDING=false deja la integración bloqueada y no acredita aceptación.

## Citas del resultado

Custom Search debe conservar las citas de páginas originales. No exige un enlace público a la consulta: esa obligación específica pertenece a Bing Search estándar. Se conservan los enlaces de consulta sólo si el servicio los devuelve; no se reconstruyen desde requesturl ni se convierten parámetros internos en referencias.

La aplicación exige una llamada real de Bing Custom Search, uso dentro de límites y al menos una cita oficial. El agente actual configura bing_custom_search_preview por Responses; el lector clásico se conserva sólo para históricos. Mantiene URL y título de las referencias aceptadas en su forma original. La captura HTTP oficial complementa el grounding; no lo sustituye. [Requisitos oficiales de visualización](https://www.microsoft.com/en-us/bing/apis/grounding-legal-enterprise#appendix-to-tou-grounding-with-bing-search-and-grounding-with-bing-custom-search-use-and-display-requirements).

## Qué demuestra cada comprobación

Un build Bicep o un az deployment group validate Succeeded no demuestra que Bing pueda crearse. En la suscripción del laboratorio, el 09/11/2026 el preflight ProviderNoRbac aceptó tanto G2 como INVALID_DIAGNOSTIC_SKU. Por tanto no se usa ese preflight como evidencia de elegibilidad o provisión real.

El inventario y GET del recurso deben mostrar la cuenta G2, su configuración y la conexión correctas. Antes de repetir un PUT, comprobar estado terminal y GET de la misma operación. Un timeout conserva incertidumbre: no reenviar automáticamente. Los tres intentos históricos con G1 devolvieron InternalServerError/Unexpected error y terminaron sin recurso. El SKU era incorrecto según el ejemplo oficial; todavía no está comprobado que corregirlo resuelva aquel fallo del proveedor.

La lectura actual indica suscripción Enabled, PayAsYouGo_2014-09-01, spendingLimit Off y proveedor Microsoft.Bing Registered. No justifica afirmar que ésta sea una suscripción gratuita no elegible. No modificar ofertas, permisos, registros o seguridad para eludir un error.

## Provisión observada y verificación del recorrido

El09/11/2026 azd provision terminó correctamente conG2/API2020-06-10, configuración y conexión. Los GET confirman cuenta Succeeded, los tres dominios permitidos y authType ApiKey/category GroundingWithCustomSearch. La continuación fue registrada y aprobada en MVP Labs con nueva ventana hasta11/11 10:00Europe/Madrid. El primer run clásico terminó failed/server_error con0 tokens y0 pasos. Se conservó su evidencia y reserva y se migró a un único agente prompt actual de Projects2.10/Responses, manteniendo modelo, conexión e instancia. Estos hechos de configuración no sustituyen las llamadas reales del recorrido ni la QA independiente.

Para consumir y comprobar el producto: provision primero, deploy después, comprobar SHA servido, inicializar acceso protegido y completar versión → fuentes reales → agente Luna/Bing → revisión humana → Decision-1 → informes. Sólo esa evidencia permite entregar ready_for_test y congelar un SHA para reproducción independiente.
