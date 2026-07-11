# API interna (Fase 4)

API HTTP **opcional y separada de la UI** sobre la capa de aplicación (`app/`),
para que ERP/portales u otras interfaces consuman las operaciones controladas.
FastAPI; versionada bajo `/v1`; documentación automática en `/docs`.

```
ERP / portal / UI  →  api/ (FastAPI, /v1)  →  app/ (casos de uso)  →  engine/ · extraction/
```

## Puesta en marcha

```bash
pip install -r requirements-api.txt
uvicorn ap_control_tower.api.main:app --port 8000
# docs interactivos: http://localhost:8000/docs   ·   OpenAPI: /openapi.json
```

## Endpoints (v1)

| Método | Ruta | Operación |
|---|---|---|
| POST | `/v1/runs` | Crear/iniciar una corrida del mes (idempotente por `run_id`) |
| GET | `/v1/runs/{id}` | Estado/resumen de la corrida |
| GET | `/v1/runs/{id}/metrics` | Métricas operativas |
| GET | `/v1/runs/{id}/documents` | Listar documentos (paginado, banca enmascarada) |
| GET | `/v1/runs/{id}/documents/{invoice_id}` | Factura y campos extraídos |
| GET | `/v1/runs/{id}/exceptions` | Listar excepciones |
| POST | `/v1/runs/{id}/exceptions/{invoice_id}/resolve` | Resolver excepción (auditado) |
| POST | `/v1/runs/{id}/documents/{invoice_id}/review` | Corrección humana (datos internos / anticipo) |
| GET | `/v1/runs/{id}/batches` | Listar lotes y su estado |
| POST | `/v1/runs/{id}/batches/{iso}/approve` | Aprobar y liberar (gate; idempotente) |
| POST | `/v1/runs/{id}/batches/{iso}/reject` | Rechazar y devolver |
| POST | `/v1/runs/{id}/batches/{iso}/close` | Cerrar (conciliación) |
| GET | `/v1/runs/{id}/audit` | Audit trail (paginado, cadena verificada) |
| POST | `/v1/documents` | Cargar y procesar un documento real (extracción) |
| GET | `/healthz` | Health check |

## Garantías

- **Validación estricta** (Pydantic) y **errores claros**: `409 gate_violation` /
  `illegal_transition`, `422 review_error`, `404 not_found`, todos con `correlation_id`.
- **Idempotencia**: crear corrida por `run_id`; aprobar/rechazar por header
  `Idempotency-Key` y por estado (re-aprobar un lote liberado no falla).
- **Paginación** `?page=&size=` con envoltura `{items, page, size, total}`.
- **Correlación**: header `X-Correlation-ID` (generado si falta) en cada respuesta.
- **Sin datos bancarios completos**: IBAN/cuenta/tax_id enmascarados en toda
  respuesta (datos completos quedan para RBAC, Fase 7).
- **Versionado**: prefijo `/v1`; documentación automática en `/docs`.

## Estado

El registro de corridas es in-memory (process-local); es la costura para
respaldarlo en Postgres en una fase posterior sin cambiar los endpoints.

Verificación: `python evals/test_api.py` (TestClient, sin red; SKIP si FastAPI ausente).
