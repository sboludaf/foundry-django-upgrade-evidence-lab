# Corrección r6 · pendiente de despliegue y QA

Responde a D1/M1/M2 del handoff Tester de r5 y al encargo de informe legible. El código de esta revisión prepara la tabla de revisión y la conclusión; el despliegue previo r5 no acredita estas funciones. No hay PASS de esta revisión.

La extracción de fuentes oficiales recupera cinco relaciones exactas de versión entre los tres CVEs del lote03/11/2026 y las ramas5.2/6.0. Usa frases completas, sin un rango inferior supuesto;113733 continúa sin rama5.2 documentada. GDAL y demás condiciones reales no se infieren. La salida original del agente permanece íntegra y separada de esta propuesta de campos; no se acredita una mejora de Luna mediante un resultado del extractor determinista.

La web muestra una fila por aviso, citas, ramas y condición. Confirmar/Corregir no ejecuta modelos. Guardar requiere confirmar las filas y un motivo; resolver condiciones requiere observación del entorno. Los cambios invalidan una decisión anterior y exigen recálculo explícito. El cliente API alternativo carga evidencia multilínea desde archivo UTF-8; sólo la ruta pasa por el terminal. Muestra mensajes saneados para entradas y etapas inválidas.

El informe abre con una de las tres conclusiones solicitadas, corrección candidata de la misma rama, motivos y pendientes. Se deriva de comparación revisada y decisión vigente; cualquier falta, contradicción o condición crítica desconocida conserva “Evidencia insuficiente”. HTML y JSON comparten esa derivación y no realizan llamadas al descargarse.

## Reproducción de la corrección

Desde este SHA privado y su README: desplegar con azd provision/azd deploy en el entorno propio autorizado, inicializar propietario mediante entrada protegida y recorrer la web. En el caso5.2.18 deben aparecer5.2.19 como candidata,113733 sin resolver en5.2 y GDAL pendiente hasta comprobarlo. Confirmar los avisos, guardar, solicitar Decision-1, descargar HTML/JSON, cambiar una condición y comprobar obsolescencia sin llamadas automáticas. Probar también5.2.19/6.0.9/6.0.10 y un error recuperable. Para la alternativa REST, cargar un archivo de revisión mayor de1024bytes y varias líneas; verificar que llega completo al servidor.

El Tester mantiene su propio entorno y reproduce desde el remoto/README. Los tests locales y la vista de desarrollo con un lote real guardado no son aceptación Azure. El fixture `tests/fixtures/official-2026-11-09.json` contiene extractos de documentación Django bajo la licencia conservada en DJANGO-LICENSE y una fila factual de soporte; sólo sirve para regresión y no se incluye en la imagen del producto.

## Límite operativo encontrado antes de desplegar

El09–10/11 se consumieron cinco de los seis builds adicionales conjuntos: cuatro de Implementer y uno de Tester. Queda uno; desplegar la corrección del autor y reproducirla independientemente requiere dos. El ledger del ciclo conserva7,905EUR imputados de8EUR agregados y0,095EUR disponibles, sin liquidar reservas anteriores. Es necesario que Proposal registre en la aplicación un ajuste conjunto de capacidad/coste antes de ambos despliegues. No se cambia el plan aprobado desde Implementer ni se evita su límite mediante otro mecanismo de build. Este checkout no inicia provisión, build o inferencias al aplicar las correcciones.
