"""Evals de la cola de tareas (Fase 5). exit 0 = verde. Puro: sin Celery/Redis.

Prueba la POLITICA de la cola: reintentos con backoff, timeout, dead-letter,
idempotencia (sin doble proceso), estado por tarea, motivo de fallo y
reproceso manual. Determinista (sleeper inyectado, sin esperas reales).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower.worker import (                                # noqa: E402
    JobStatus,
    JobStore,
    JobTimeout,
    RetryPolicy,
    reprocess_job,
    run_job,
)

failures: list[str] = []
NO_SLEEP = lambda _s: None  # noqa: E731  (sleeper no-op: tests deterministas)


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    print("== 1. Exito directo ==")
    store = JobStore()
    rec = run_job(store, "ok", lambda: 42, sleeper=NO_SLEEP)
    check(rec.status == JobStatus.SUCCESS and rec.result == 42 and rec.attempts == 1,
          "una tarea que anda -> success en 1 intento")

    print("== 2. Reintentos con backoff y exito eventual ==")
    delays: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError(f"fallo transitorio {calls['n']}")
        return "ok-al-tercero"

    pol = RetryPolicy(max_retries=3, base_delay=1.0, backoff=2.0)
    rec = run_job(store, "flaky", flaky, policy=pol, sleeper=delays.append)
    check(rec.status == JobStatus.SUCCESS and rec.attempts == 3,
          f"exito al 3er intento ({rec.attempts} intentos)")
    check(delays == [1.0, 2.0],
          f"backoff exponencial entre intentos: {delays} == [1.0, 2.0]")
    check(len(rec.history) == 2, "cada fallo previo quedo registrado con su motivo")

    print("== 3. Agota reintentos -> dead-letter con motivo ==")
    def siempre_falla():
        raise RuntimeError("Document AI caido")

    pol = RetryPolicy(max_retries=2, base_delay=0.1)
    rec = run_job(store, "muere", siempre_falla, policy=pol, sleeper=NO_SLEEP)
    check(rec.status == JobStatus.DEAD_LETTER and rec.attempts == 3,
          f"3 intentos (1+2 reintentos) -> dead_letter ({rec.attempts})")
    check("Document AI caido" in (rec.error or ""),
          "el motivo del fallo queda registrado en el job")
    check(rec in store.dead_letters(), "aparece en la cola de fallos (dead-letter)")

    print("== 4. Timeout por intento ==")
    import time as _t
    pol = RetryPolicy(max_retries=0, timeout=0.05)
    rec = run_job(store, "lento", lambda: _t.sleep(0.5), policy=pol, sleeper=NO_SLEEP)
    check(rec.status == JobStatus.DEAD_LETTER and "timeout" in (rec.error or "").lower(),
          "un intento que excede el timeout -> falla por timeout")

    print("== 5. Idempotencia: sin doble procesamiento ==")
    store2 = JobStore()
    ejecuciones = {"n": 0}

    def procesa():
        ejecuciones["n"] += 1
        return ejecuciones["n"]

    r1 = run_job(store2, "doc", procesa, dedup_key="sha256:AAA", sleeper=NO_SLEEP)
    r2 = run_job(store2, "doc", procesa, dedup_key="sha256:AAA", sleeper=NO_SLEEP)
    check(ejecuciones["n"] == 1 and r1.id == r2.id,
          "mismo dedup_key -> se ejecuta UNA vez (idempotente)")

    print("== 6. Reproceso manual de un dead-letter ==")
    store3 = JobStore()
    intentos = {"n": 0}

    def falla_luego_anda():
        intentos["n"] += 1
        if intentos["n"] <= 3:   # muere en la primera corrida (1+2 reintentos)
            raise RuntimeError("infra no disponible")
        return "recuperado"

    pol = RetryPolicy(max_retries=2, base_delay=0.1)
    dead = run_job(store3, "recupera", falla_luego_anda, policy=pol, sleeper=NO_SLEEP)
    check(dead.status == JobStatus.DEAD_LETTER, "primera corrida termina en dead-letter")
    again = reprocess_job(store3, dead.id, falla_luego_anda, policy=pol, sleeper=NO_SLEEP)
    check(again is not None and again.id == dead.id
          and again.status == JobStatus.SUCCESS and again.result == "recuperado",
          "reproceso manual (mismo id) recupera el job -> success")
    check(reprocess_job(store3, "job-inexistente", falla_luego_anda) is None,
          "reprocesar un id inexistente -> None")

    print()
    if failures:
        print(f"COLA ROJA: {len(failures)} fallas")
        return 1
    print("COLA VERDE: reintentos/backoff/timeout/dead-letter/idempotencia OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
