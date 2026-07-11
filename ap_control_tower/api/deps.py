"""Dependencias e infraestructura compartida de la API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Query

from ..models import Dataset, load_dataset
from .registry import RunRegistry

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_PATH = ROOT / "data" / "synthetic_month.json"

# Registro de corridas (process-local). En un futuro se respalda en Postgres.
_registry = RunRegistry()
_dataset: Dataset | None = None


def get_registry() -> RunRegistry:
    return _registry


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
