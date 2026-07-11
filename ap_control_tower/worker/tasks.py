"""Tareas Celery (transporte distribuido, Fase 5). Requiere `celery`.

Envuelven las MISMAS funciones nucleo que la ejecucion inline (service.py), con
la politica de reintentos nativa de Celery (backoff exponencial acotado, tope de
reintentos). Al agotar reintentos, ``on_failure`` registra el motivo (dead-letter
observable en el backend de resultados / logs). Los documentos viajan como base64
en el mensaje; la respuesta ya trae los datos bancarios enmascarados.
"""

from __future__ import annotations

import base64
import logging

from celery import Task

from .celery_app import celery_app
from .service import process_document_core

log = logging.getLogger("ap_control_tower.worker")


class _BaseTask(Task):
    autoretry_for = (Exception,)
    max_retries = 3
    retry_backoff = True          # espera progresiva entre reintentos
    retry_backoff_max = 30
    retry_jitter = False
    acks_late = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # pragma: no cover
        # Motivo de fallo tras agotar reintentos: queda logueado (dead-letter).
        log.error("tarea %s a dead-letter tras agotar reintentos: %s: %s",
                  self.name, type(exc).__name__, exc)


@celery_app.task(bind=True, base=_BaseTask, name="ap.process_document")
def process_document_task(self, filename: str, data_b64: str) -> dict:
    """Procesa un documento (extraccion + enmascarado) en un worker."""
    data = base64.b64decode(data_b64)
    return process_document_core(filename, data)
