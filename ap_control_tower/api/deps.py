"""Dependencias e infraestructura compartida de la API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import Query

from ..models import Dataset, load_dataset
from ..worker import JobService
from .registry import RunRegistry

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_PATH = ROOT / "data" / "synthetic_month.json"

_BROKER_ENV = ("AP_BROKER_URL", "CELERY_BROKER_URL")

# Registro de corridas (process-local). En un futuro se respalda en Postgres.
_registry = RunRegistry()
_dataset: Dataset | None = None
# Cola de tareas: se elige en el primer uso segun el entorno (ver _build_job_service).
_jobs = None


def get_registry() -> RunRegistry:
    return _registry


def _build_job_service():
    """Elige el despacho de la cola segun el entorno (Fase 5.1).

    - Con broker configurado (``AP_BROKER_URL``) y sin modo eager: el
      procesamiento largo se ENCOLA en Celery y corre en un worker separado
      (la request web devuelve 202 sin bloquear).
    - Sin broker (o en eager): ejecucion inline, mismo contrato de tarea/estado,
      sin necesidad de infraestructura (demo/tests).
    """
    eager = os.environ.get("AP_CELERY_EAGER", "").lower() in ("1", "true", "yes")
    if any(os.environ.get(n) for n in _BROKER_ENV) and not eager:
        from ..worker.celery_service import CeleryJobService
        return CeleryJobService()
    return JobService()


def get_job_service():
    global _jobs
    if _jobs is None:
        _jobs = _build_job_service()
    return _jobs


def get_dataset() -> Dataset:
    global _dataset
    if _dataset is None:
        _dataset = load_dataset(str(DATASET_PATH))
    return _dataset


@dataclass
class Pagination:
    page: int
    size: int

    @property
    def start(self) -> int:
        return (self.page - 1) * self.size

    @property
    def end(self) -> int:
        return self.start + self.size


def pagination(page: int = Query(1, ge=1, description="Pagina (1-indexada)"),
               size: int = Query(50, ge=1, le=200, description="Tamanio de pagina")) -> Pagination:
    return Pagination(page=page, size=size)
