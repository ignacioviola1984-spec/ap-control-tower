# Cola de tareas y workers (Fase 5)

Procesamiento asíncrono para sacar el trabajo largo (extracción / Document AI /
controles) de la request web. **Aditivo y opcional**: el núcleo de la cola es
puro y se usa/testea sin infraestructura; Celery + Redis son el transporte
distribuido para Docker/Cloud.

```
API  →  JobService  →  runner (política)  →  núcleo (extracción/controles)
                         │
             Celery + Redis (transporte distribuido, workers separados)
```

| Módulo | Rol |
|---|---|
| `jobs.py` | `JobStore` + `JobRecord` + `JobStatus` + `RetryPolicy` (puro, thread-safe). |
| `runner.py` | Ejecuta la política: idempotencia (dedup), reintentos con backoff, timeout por intento, dead-letter, reproceso manual. Puro. |
| `service.py` | `JobService`: `submit_document`, `get`, `dead_letters`, `reprocess`. Inline por defecto; con Celery corre en workers. |
| `celery_app.py` · `tasks.py` | Transporte Celery (requiere `requirements-worker.txt`). Mismas funciones núcleo. |

## Garantías (todas bajo eval)

- **Reintentos con espera progresiva** (backoff exponencial acotado) y **tope**.
- **Timeout** por intento; **dead-letter** al agotar reintentos, con **motivo**.
- **Idempotencia**: un documento con el mismo contenido no se re-procesa.
- **Estado visible** por tarea (`GET /v1/tasks/{id}`) y **reproceso manual
  autorizado** (`POST /v1/tasks/{id}/reprocess`).
- **No bloquear la interfaz**: la API responde `202 + task_id`; con Celery el
  trabajo corre en un worker separado.

## Despacho asincrono real (Fase 5.1)

Con `AP_BROKER_URL` configurado, `POST /v1/documents` **encola** en Celery
(`CeleryJobService`) y devuelve `202 + task_id` de inmediato, en estado
`queued`: la extraccion pesada la corre el worker, **no** la request web. Sin
broker (o `AP_CELERY_EAGER=1`) se usa el `JobService` inline (demo/tests, sin
infra). La seleccion vive en `api/deps.py`; la **idempotencia por contenido**
(hash) se preserva antes de despachar. Verificado en vivo contra Redis real
(worker separado ejecuta la tarea) y en `evals/test_worker_dispatch.py`.

## Puesta en marcha (dev, WSL)

```bash
pip install -r requirements-worker.txt
docker compose -f docker-compose.dev.yml up -d redis worker    # Redis + worker Celery
export AP_BROKER_URL=redis://localhost:6379/0
# la API despacha a la cola automaticamente cuando AP_BROKER_URL esta seteado
```

Sin `AP_BROKER_URL` (o con `AP_CELERY_EAGER=1`) todo corre inline/eager, sin infra.

## Despliegue en Google Cloud (a JUSTIFICAR antes de desplegar)

Los **workers persistentes NO encajan** naturalmente en un servicio Cloud Run
que escala a 0 (no hay proceso vivo consumiendo la cola). Opciones recomendadas,
a decidir con el usuario antes de tocar infraestructura:

1. **Cloud Run Jobs** para tareas por lote / mantenimiento (p. ej. la corrida
   del mes, reprocesos masivos): se disparan y terminan; encajan con escala a 0.
2. **Servicio worker separado con `min-instances=1`** (Cloud Run service o GKE
   Autopilot) consumiendo Redis (Memorystore) para procesamiento continuo de
   documentos. Costo fijo por la instancia mínima.
3. **Cloud Tasks + endpoints push** como alternativa administrada sin worker
   persistente: la cola invoca un endpoint HTTP del servicio (que sí puede
   escalar a 0), evitando un broker Redis. Buena opción si el volumen es bajo.

La demo actual (Streamlit) y su despliegue **no cambian**: esta cola es
infraestructura nueva y separada. No se despliega nada sin autorización.

## Verificación

```bash
python evals/test_worker.py          # política de la cola (puro, sin infra)
python evals/test_worker_celery.py   # transporte Celery en eager (SKIP sin celery)
```
