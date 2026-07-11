"""Despacho ASINCRONO real a Celery (Fase 5.1).

Cierra el gap de la Fase 5: con broker configurado, `submit_document` **encola**
la tarea en Celery y devuelve de inmediato el `JobRecord` en estado `queued`
(NO ejecuta la extraccion pesada en la request web). El worker separado la
procesa; `get()` reconcilia el estado contra el `AsyncResult` (backend de
resultados). Sin broker se sigue usando el `JobService` inline (ver deps.py).

Preserva la **idempotencia por contenido** (hash sha256): antes de despachar se
reusa un job existente con la misma clave que no haya ido a dead-letter (exitoso
o aun en curso) — Celery por si mismo no deduplica por contenido.

Celery se importa PEREZOSAMENTE (en `_ensure_transport`): importar este modulo
no requiere celery/redis, y el transporte es inyectable para tests hermeticos.
Los bytes crudos del documento NO se guardan en el JobRecord (privacidad); el
reproceso reusa un thunk en memoria, igual que el servicio inline.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Callable

from .jobs import TERMINAL, JobRecord, JobStatus, JobStore
from .service import DOC_POLICY

# Estados de Celery -> estado propio de la cola.
_CELERY_SUCCESS = "SUCCESS"
_CELERY_FAILURE = "FAILURE"
_CELERY_RETRY = "RETRY"
_CELERY_STARTED = "STARTED"


class CeleryJobService:
    """Punto de entrada de la cola para la API cuando hay broker configurado.

    Interfaz identica al `JobService` inline (submit_document/get/dead_letters/
    reprocess), pero el trabajo lo corre un worker Celery separado.
    """

    def __init__(self, store: JobStore | None = None,
                 task: Any = None,
                 result_for: Callable[[str], Any] | None = None) -> None:
        self.store = store or JobStore()
        self._task = task                # objeto con .apply_async(args, task_id=...)
        self._result_for = result_for    # callable(id) -> AsyncResult-like
        self._reinvoke: dict[str, tuple] = {}   # job_id -> (filename, data)

    # -------------------------------------------------- transporte (perezoso)
    def _ensure_transport(self) -> None:
        if self._task is not None and self._result_for is not None:
            return
        from .celery_app import celery_app
        from .tasks import process_document_task
        if self._task is None:
            self._task = process_document_task
        if self._result_for is None:
            self._result_for = celery_app.AsyncResult

    # -------------------------------------------------- documentos
    def submit_document(self, filename: str, data: bytes) -> JobRecord:
        """Encola el procesamiento en Celery y devuelve el JobRecord `queued`
        SIN bloquear. Idempotente por hash del contenido."""
        dedup = "sha256:" + hashlib.sha256(data).hexdigest()
        existing = self.store.find_by_dedup(dedup)
        if existing is not None:
            return existing            # idempotencia: no re-despacha
        self._ensure_transport()
        rec = self.store.create(name="process_document",
                                max_attempts=DOC_POLICY.max_attempts,
                                dedup_key=dedup)
        b64 = base64.b64encode(data).decode()
        # task_id == rec.id: permite reconciliar el estado sin mapa lateral.
        self._task.apply_async(args=[filename, b64], task_id=rec.id)
        self._reinvoke.setdefault(rec.id, (filename, data))
        return rec

    # -------------------------------------------------- consulta / reproceso
    def get(self, job_id: str) -> JobRecord | None:
        rec = self.store.get(job_id)
        if rec is None:
            return None
        return self._reconcile(rec)

    def dead_letters(self) -> list[JobRecord]:
        for rec in self.store.all():
            self._reconcile(rec)
        return self.store.dead_letters()

    def reprocess(self, job_id: str, requested_by: str) -> JobRecord | None:
        """Reproceso MANUAL autorizado: re-despacha a Celery reusando el mismo id."""
        rec = self.store.get(job_id)
        if rec is None:
            return None
        if job_id not in self._reinvoke:
            return rec                 # sin payload disponible para reprocesar
        self._ensure_transport()
        self.store.reset_for_reprocess(rec)
        filename, data = self._reinvoke[job_id]
        b64 = base64.b64encode(data).decode()
        self._task.apply_async(args=[filename, b64], task_id=rec.id)
        return rec

    # -------------------------------------------------- reconciliacion
    def _reconcile(self, rec: JobRecord) -> JobRecord:
        """Sincroniza el JobRecord con el estado real de la tarea Celery."""
        if rec.status in TERMINAL:
            return rec
        self._ensure_transport()
        ar = self._result_for(rec.id)
        state = getattr(ar, "state", None)
        if state == _CELERY_SUCCESS:
            self.store.mark_success(rec, ar.result)
        elif state == _CELERY_FAILURE:
            self.store.mark_dead_letter(rec, _describe(ar.result))
        elif state == _CELERY_RETRY:
            self.store.mark_retrying(rec)
        elif state == _CELERY_STARTED:
            self.store.mark_running(rec, rec.attempts or 1)
        # PENDING / RECEIVED -> permanece 'queued'
        return rec


def _describe(payload: Any) -> str:
    if isinstance(payload, BaseException):
        return f"{type(payload).__name__}: {payload}"
    return str(payload)
