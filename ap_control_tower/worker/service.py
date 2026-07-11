"""JobService: orquesta las tareas de negocio sobre la politica de cola (Fase 5).

Por defecto ejecuta INLINE (via runner): la API obtiene un task_id y estado
aunque no haya broker (contrato asincrono degradado, testeable sin infra). Con
Celery/Redis configurado, las mismas funciones nucleo corren en workers
separados (ver celery_app/tasks). Los bytes crudos del documento NO se guardan
en el JobRecord (privacidad): el reproceso reusa un thunk en memoria.
"""

from __future__ import annotations

import hashlib
import time
from typing import Callable

from .. import app as appsvc
from ..persistence.masking import mask_account, mask_iban
from .jobs import JobRecord, JobStore, RetryPolicy
from .runner import reprocess_job, run_job

DOC_POLICY = RetryPolicy(max_retries=3, base_delay=0.5, backoff=2.0,
                         max_delay=30.0, timeout=90.0)


def process_document_core(filename: str, data: bytes) -> dict:
    """Nucleo de procesamiento de un documento: extraccion + enmascarado.

    Es la unidad que corre en la cola (misma funcion inline o en Celery). Puede
    lanzar excepcion (Document AI caido, PDF invalido): la politica reintenta y,
    si agota, manda a dead-letter con el motivo.
    """
    result = appsvc.process_uploaded_document(filename, data)
    doc = dict(result.document)
    if doc.get("iban"):
        doc["iban"] = mask_iban(doc["iban"])
    if doc.get("proveedor_cuenta_bancaria"):
        doc["proveedor_cuenta_bancaria"] = mask_account(doc["proveedor_cuenta_bancaria"])
    return {"archivo": result.doc_id, "motor": result.engine,
            "document_type": doc.get("document_type"),
            "confidence": str(result.confidence), "pages": result.pages,
            "warnings": result.warnings, "document": doc}


class JobService:
    """Punto de entrada de la cola para la API y tareas de mantenimiento."""

    def __init__(self, store: JobStore | None = None, policy: RetryPolicy | None = None,
                 sleeper: Callable[[float], None] = time.sleep,
                 core: Callable[[str, bytes], dict] = process_document_core) -> None:
        self.store = store or JobStore()
        self.policy = policy or DOC_POLICY
        self.sleeper = sleeper
        self._core = core
        self._reinvoke: dict[str, tuple] = {}   # job_id -> (fn, args)

    # -------------------------------------------------- documentos
    def submit_document(self, filename: str, data: bytes) -> JobRecord:
        """Encola el procesamiento de un documento. Idempotente por hash del
        contenido: subir el MISMO archivo no re-procesa."""
        dedup = "sha256:" + hashlib.sha256(data).hexdigest()
        rec = run_job(self.store, "process_document", self._core,
                      args=(filename, data), dedup_key=dedup,
                      policy=self.policy, sleeper=self.sleeper)
        self._reinvoke.setdefault(rec.id, (self._core, (filename, data)))
        return rec

    # -------------------------------------------------- consulta / reproceso
    def get(self, job_id: str) -> JobRecord | None:
        return self.store.get(job_id)

    def dead_letters(self) -> list[JobRecord]:
        return self.store.dead_letters()

    def reprocess(self, job_id: str, requested_by: str) -> JobRecord | None:
        """Reproceso MANUAL autorizado de un job dead-letter. Requiere que el
        payload original siga disponible (thunk en memoria)."""
        if job_id not in self._reinvoke:
            rec = self.store.get(job_id)
            return rec  # sin payload para reprocesar: se devuelve tal cual
        fn, args = self._reinvoke[job_id]
        return reprocess_job(self.store, job_id, fn, args=args,
                             policy=self.policy, sleeper=self.sleeper)
