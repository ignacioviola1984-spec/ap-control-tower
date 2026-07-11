"""App Celery (transporte distribuido opcional, Fase 5). Requiere `celery`.

Broker/backend desde entorno (Redis por defecto). Si no hay broker o
``AP_CELERY_EAGER=1``, corre en modo eager (sincrono, sin infra) — util para
tests y para un arranque degradado. Config pensada para Docker/Cloud Run:
acks_late + reject_on_worker_lost (redelivery si el worker muere), limites de
tiempo (soft/hard) y prefetch 1 (reparto justo).

Arranque del worker (con Redis levantado):
    celery -A ap_control_tower.worker.celery_app:celery_app worker --loglevel=info
"""

from __future__ import annotations

import os

from celery import Celery

BROKER_ENV = ("AP_BROKER_URL", "CELERY_BROKER_URL")
BACKEND_ENV = ("AP_RESULT_BACKEND", "CELERY_RESULT_BACKEND")
_DEFAULT_REDIS = "redis://localhost:6379/0"


def _from_env(names: tuple[str, ...], default: str) -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def _eager() -> bool:
    if os.environ.get("AP_CELERY_EAGER", "").lower() in ("1", "true", "yes"):
        return True
    # sin broker explicito -> eager (degradado, sin infra)
    return not any(os.environ.get(n) for n in BROKER_ENV)


def build_celery() -> Celery:
    broker = _from_env(BROKER_ENV, _DEFAULT_REDIS)
    backend = _from_env(BACKEND_ENV, broker)
    app = Celery("ap_control_tower", broker=broker, backend=backend,
                 include=["ap_control_tower.worker.tasks"])
    eager = _eager()
    app.conf.update(
        task_always_eager=eager,
        task_eager_propagates=False,     # en eager, las fallas no revientan el caller
        task_acks_late=True,             # redelivery si el worker cae a mitad
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,    # reparto justo entre workers
        task_soft_time_limit=90,         # timeout suave por tarea (s)
        task_time_limit=120,             # timeout duro
        task_default_retry_delay=2,
        broker_connection_retry_on_startup=True,
        result_expires=3600,
    )
    return app


celery_app = build_celery()
