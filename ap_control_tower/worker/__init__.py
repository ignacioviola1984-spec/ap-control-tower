"""Cola de tareas y workers de AP Control Tower (Fase 5).

El nucleo (jobs/runner/service) es puro: no importa Celery ni Redis y se puede
usar y testear sin infra. ``celery_app`` y ``tasks`` (transporte distribuido)
son opcionales y requieren las dependencias de requirements-worker.txt.
"""

from __future__ import annotations

from .jobs import JobRecord, JobStatus, JobStore, RetryPolicy
from .runner import JobTimeout, reprocess_job, run_job
from .service import JobService, process_document_core

__all__ = [
    "JobRecord", "JobStatus", "JobStore", "RetryPolicy",
    "JobTimeout", "run_job", "reprocess_job",
    "JobService", "process_document_core",
]
