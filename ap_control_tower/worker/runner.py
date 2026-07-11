"""Ejecutor de la politica de cola (Fase 5). Puro: sin Celery ni Redis.

Aplica: idempotencia (dedup), reintentos con espera progresiva, timeout por
intento, dead-letter al agotar reintentos y registro del motivo de fallo. El
mismo nucleo lo usan el JobService (inline) y las tareas Celery (distribuido).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .jobs import JobRecord, JobStore, RetryPolicy


class JobTimeout(RuntimeError):
    """Un intento supero el timeout configurado."""


def _call_with_timeout(fn: Callable, args: tuple, kwargs: dict,
                       timeout: float | None) -> Any:
    """Ejecuta fn; si supera `timeout` (s) levanta JobTimeout.

    Cross-platform via hilo (no se puede matar el hilo: el trabajo huerfano se
    documenta; en produccion Celery lo cubre con soft_time_limit). None = sin
    limite (ejecucion directa)."""
    if timeout is None:
        return fn(*args, **kwargs)

    import threading

    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except Exception as exc:  # se re-lanza en el hilo principal
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise JobTimeout(f"el intento supero el timeout de {timeout}s")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_job(
    store: JobStore, name: str, fn: Callable, *,
    args: tuple = (), kwargs: dict | None = None,
    dedup_key: str | None = None, policy: RetryPolicy | None = None,
    sleeper: Callable[[float], None] = time.sleep, job_id: str | None = None,
) -> JobRecord:
    """Corre una tarea con la politica de cola y devuelve su JobRecord final.

    - Idempotencia: si ya hay un job EXITOSO con `dedup_key`, se devuelve ese
      (no se re-procesa).
    - Reintentos: hasta `policy.max_retries`, con backoff entre intentos.
    - Dead-letter: al agotar reintentos, estado dead_letter con el motivo.
    """
    policy = policy or RetryPolicy()
    kwargs = kwargs or {}

    if dedup_key:
        existing = store.find_success_by_dedup(dedup_key)
        if existing is not None:
            return existing

    rec = store.create(name=name, max_attempts=policy.max_attempts,
                       dedup_key=dedup_key, job_id=job_id)
    attempt = 0
    while True:
        attempt += 1
        store.mark_running(rec, attempt)
        try:
            result = _call_with_timeout(fn, args, kwargs, policy.timeout)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            store.record_failure(rec, attempt, reason)
            if attempt >= policy.max_attempts:      # agoto reintentos
                store.mark_dead_letter(rec, reason)
                return rec
            store.mark_retrying(rec)
            sleeper(policy.delay_for(attempt))
            continue
        store.mark_success(rec, result)
        return rec


def reprocess_job(
    store: JobStore, job_id: str, fn: Callable, *,
    args: tuple = (), kwargs: dict | None = None,
    policy: RetryPolicy | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> JobRecord | None:
    """Reproceso MANUAL de un job dead-letter (o fallido). Reusa el mismo id.
    Devuelve None si el job no existe o no es reprocesable."""
    rec = store.get(job_id)
    if rec is None:
        return None
    from .jobs import JobStatus
    if rec.status != JobStatus.DEAD_LETTER:
        return rec
    store.reset_for_reprocess(rec)
    # re-ejecuta con el mismo id (dedup no aplica: es un reproceso explicito)
    policy = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        store.mark_running(rec, attempt)
        try:
            result = _call_with_timeout(fn, args, kwargs or {}, policy.timeout)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            store.record_failure(rec, attempt, reason)
            if attempt >= policy.max_attempts:
                store.mark_dead_letter(rec, reason)
                return rec
            store.mark_retrying(rec)
            sleeper(policy.delay_for(attempt))
            continue
        store.mark_success(rec, result)
        return rec
