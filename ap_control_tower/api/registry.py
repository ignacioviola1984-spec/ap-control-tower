"""Registro de corridas de la API (Fase 4).

Estado del lado del servidor entre requests HTTP. Por defecto in-memory
(process-local): cada corrida se guarda por su run_id. Es la COSTURA para, en
una fase posterior, respaldar el estado en Postgres sin cambiar los endpoints.

Incluye un cache de idempotencia simple (por run_id + clave de idempotencia)
para operaciones sensibles (aprobar/rechazar/cerrar/crear corrida).
"""

from __future__ import annotations

import threading
from typing import Any

from .. import app as appsvc
from ..models import Dataset


class RunRegistry:
    """Guarda RunState por run_id. Thread-safe (la API puede ser multihilo)."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[tuple[str, str], Any] = {}
        self._lock = threading.RLock()

    # -------------------------------------------------- corridas
    def create_run(self, dataset: Dataset, run_id: str | None = None) -> dict[str, Any]:
        """Procesa el mes y guarda la corrida. Si run_id ya existe, la devuelve
        (idempotente por run_id)."""
        with self._lock:
            if run_id and run_id in self._runs:
                return self._runs[run_id]
            run = appsvc.process_month(dataset, run_id=run_id)
            rid = run["result"].run_id
            self._runs[rid] = run
            return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_run_ids(self) -> list[str]:
        with self._lock:
            return list(self._runs)

    # -------------------------------------------------- idempotencia
    def idempotent(self, run_id: str, key: str | None):
        """Devuelve el resultado cacheado para (run_id, key) o None."""
        if not key:
            return None
        with self._lock:
            return self._idempotency.get((run_id, key))

    def remember(self, run_id: str, key: str | None, value: Any) -> None:
        if not key:
            return
        with self._lock:
            self._idempotency[(run_id, key)] = value

    def lock(self) -> threading.RLock:
        return self._lock
