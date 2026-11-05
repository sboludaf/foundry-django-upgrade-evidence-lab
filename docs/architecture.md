# Arquitectura al 05/11/2026

```mermaid
flowchart LR
  ARM[azd provision / ARM] --> A[AIServices: creado]
  A --> L[django-luna: creado, no invocado]
  A --> D[django-decision: creado, no invocado]
  A --> P[Proyecto django-upgrade: creado]
  O[Operador] --> R[Azure AI User: asignado en cuenta propia]
  R --> A
  ARM -. error 500 .-> B[Bing Custom Search: no creado]
  B -. pendiente .-> C[Conexión e instancia: no creadas]
```

Este diagrama se acredita con readbacks ARM y `provisioning-evidence.json`. No representa llamadas de análisis ni una aplicación web en Azure.

Objetivo pendiente: navegador autenticado → Container Apps → agente Luna + Bing Custom Search → revisión/comparador → Decision-1 → informes HTML/JSON. Blob/Table conservarán etapas, evidencia y ledger; ACR servirá la imagen. Esos componentes de hosting, datos y producto no están desplegados ni acreditados. Una futura copia Tester usará otro grupo y otros recursos.
