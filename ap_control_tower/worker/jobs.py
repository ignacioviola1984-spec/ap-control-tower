"""Estado y politica de la cola de tareas (Fase 5). Puro: sin Celery ni Redis.

Este modulo define QUE garantiza la cola (estados, reintentos, dead-letter,
idempotencia); ``runner.py`` la ejecuta y ``celery_app``/``tasks`` son el
transporte distribuido opcional. El JobStore es in-memory y thread-safe: es la
costura para respaldarlo en Redis/Postgres sin cambiar la interfaz.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCESS = "success"
    DEAD_LETTER = "dead_letter"   # agoto reintentos: requiere reproceso manual


TERMINAL = frozenset({JobStatus.SUCCESS, JobStatus.DEAD_LETTER})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class RetryPolicy:
    """Reintentos con espera progresiva (backoff exponencial acotado)."""
    max_retries: int = 3
    base_delay: float = 0.5
    backoff: float = 2.0
    max_delay: float = 30.0
    timeout: float | None = None   # segundos por intento (None = sin limite)

    def delay_for(self, attempt: int) -> float:
        """Espera antes del intento nro `attempt` (1-indexado)."""
        d = self.base_delay * (self.backoff ** (attempt - 1))
        return float(min(d, self.max_delay))

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1


@dataclass
class JobRecord:
    id: str
    name: str
    status: str
    max_attempts: int
    dedup_key: str | None = None
    attempts: int = 0
    result: Any = None
    error: str | None = None          # motivo del ultimo fallo
    history: list[dict] = field(default_factory=list)   # intento -> motivo
    created_ts: str = field(default_factory=_now)
    updated_ts: str = field(default_factory=_now)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "attempts": self.attempts, "max_attempts": self.max_attempts,
            "dedup_key": self.dedup_key, "error": self.error,
            "history": list(self.history),
            "created_ts": self.created_ts, "updated_ts": self.updated_ts,
        }


class JobStore:
    """Almacen de tareas thread-safe (in-memory por defecto)."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def create(self, name: str, max_attempts: int, dedup_key: str | None = None,
               job_id: str | None = None) -> JobRecord:
        with self._lock:
            jid = job_id or f"job-{uuid.uuid4().hex[:16]}"
            rec = JobRecord(id=jid, name=name, status=JobStatus.QUEUED,
                            max_attempts=max_attempts, dedup_key=dedup_key)
            self._jobs[jid] = rec
            return rec

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_success_by_dedup(self, dedup_key: str) -> JobRecord | None:
        """Idempotencia: primer job exitoso con esa clave (evita doble proceso)."""
        with self._lock:
            for rec in self._jobs.values():
                if rec.dedup_key == dedup_key and rec.status == JobStatus.SUCCESS:
                    return rec
            return None

    def dead_letters(self) -> list[JobRecord]:
        with self._lock:
            return [r for r in self._jobs.values() if r.status == JobStatus.DEAD_LETTER]

    def all(self) -> list[JobRecord]:
        with self._lock:
            return list(self._jobs.values())

    # -- transiciones de estado (todas timestampan) --
    def _touch(self, rec: JobRecord) -> None:
        rec.updated_ts = _now()

    def mark_running(self, rec: JobRecord, attempt: int) -> None:
        with self._lock:
            rec.status = JobStatus.RUNNING
            rec.attempts = attempt
            self._touch(rec)

    def record_failure(self, rec: JobRecord, attempt: int, reason: str) -> None:
        with self._lock:
            rec.error = reason
            rec.history.append({"attempt": attempt, "reason": reason, "ts": _now()})
            self._touch(rec)

    def mark_retrying(self, rec: JobRecord) -> None:
        with self._lock:
            rec.status = JobStatus.RETRYING
            self._touch(rec)

    def mark_success(self, rec: JobRecord, result: Any) -> None:
        with self._lock:
            rec.status = JobStatus.SUCCESS
            rec.result = result
            rec.error = None
            self._touch(rec)

    def mark_dead_letter(self, rec: JobRecord, reason: str) -> None:
        with self._lock:
            rec.status = JobStatus.DEAD_LETTER
            rec.error = reason
            self._touch(rec)

    def reset_for_reprocess(self, rec: JobRecord) -> None:
        with self._lock:
            rec.status = JobStatus.QUEUED
            rec.attempts = 0
            rec.error = None
            self._touch(rec)
