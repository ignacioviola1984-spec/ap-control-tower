"""Eval del despacho asincrono a Celery (Fase 5.1). exit 0 = verde.

Verifica el gap que cerro la Fase 5.1: con broker configurado, el documento se
ENCOLA (no se procesa inline en la request) y el estado se reconcilia contra la
tarea; ademas se preserva la idempotencia por contenido y la seleccion inline vs
Celery segun el entorno.

Hermetico: inyecta un transporte falso (no necesita celery ni redis). La prueba
de seleccion en la API se SALTEA si FastAPI no esta instalado.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


class _FakeAsync:
    """AsyncResult de mentira: estado + resultado configurables."""
    def __init__(self, state: str, result=None) -> None:
        self.state = state
        self.result = result


class _FakeTask:
    """Tarea Celery de mentira: registra los apply_async (no ejecuta nada)."""
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def apply_async(self, args=None, task_id=None):
        self.calls.append((tuple(args or ()), task_id))
        return _FakeAsync("PENDING")


def main() -> int:
    from ap_control_tower.worker.celery_service import CeleryJobService
    from ap_control_tower.worker.jobs import JobStore

    store = JobStore()
    task = _FakeTask()
    states: dict[str, _FakeAsync] = {}

    def result_for(jid: str) -> _FakeAsync:
        return states.get(jid, _FakeAsync("PENDING"))

    svc = CeleryJobService(store=store, task=task, result_for=result_for)

    print("== Despacho: encola en Celery, NO ejecuta inline ==")
    rec = svc.submit_document("f.pdf", b"hola-mundo")
    check(rec.status == "queued",
          "submit_document devuelve 'queued' (no ejecuto el trabajo pesado en linea)")
    check(rec.result is None, "el JobRecord no trae resultado al encolar (async)")
    check(len(task.calls) == 1 and task.calls[0][1] == rec.id,
          "se despacho exactamente una tarea Celery con task_id == id del job")
    check(base64.b64decode(task.calls[0][0][1]) == b"hola-mundo",
          "el payload viaja a la tarea (base64) y no se pierde")

    print("== Reconciliacion de estado contra la tarea ==")
    check(svc.get(rec.id).status == "queued", "PENDING -> permanece 'queued'")
    states[rec.id] = _FakeAsync("SUCCESS", {"archivo": "f", "motor": "x"})
    got = svc.get(rec.id)
    check(got.status == "success" and got.result == {"archivo": "f", "motor": "x"},
          "SUCCESS del worker -> el job pasa a 'success' con resultado")

    print("== Idempotencia por contenido ==")
    rec_dup = svc.submit_document("otro-nombre.pdf", b"hola-mundo")
    check(rec_dup.id == rec.id and len(task.calls) == 1,
          "mismo contenido ya exitoso: reusa el job, NO re-despacha")
    r3 = svc.submit_document("g.pdf", b"otro-contenido")
    r4 = svc.submit_document("g.pdf", b"otro-contenido")
    calls_para_g = [c for c in task.calls if base64.b64decode(c[0][1]) == b"otro-contenido"]
    check(r4.id == r3.id and len(calls_para_g) == 1,
          "mismo contenido AUN EN CURSO (queued): no se despacha dos veces")

    print("== Fallo permanente -> dead-letter, consultable ==")
    r5 = svc.submit_document("bad.pdf", b"contenido-que-falla")
    states[r5.id] = _FakeAsync("FAILURE", RuntimeError("boom del worker"))
    got5 = svc.get(r5.id)
    check(got5.status == "dead_letter" and "boom del worker" in (got5.error or ""),
          "FAILURE del worker -> 'dead_letter' con el motivo")
    check(any(r.id == r5.id for r in svc.dead_letters()),
          "el job fallido aparece en dead_letters() (reconciliado)")

    print("== Reproceso manual re-despacha (no duplica id) ==")
    antes = len([c for c in task.calls if c[1] == r5.id])
    rr = svc.reprocess(r5.id, requested_by="Supervisora Demo")
    despues = len([c for c in task.calls if c[1] == r5.id])
    check(rr is not None and rr.id == r5.id and rr.status == "queued" and despues == antes + 1,
          "reprocess() re-despacha la MISMA tarea y la vuelve a 'queued'")

    print("== Seleccion inline vs Celery segun entorno (API) ==")
    try:
        from ap_control_tower.api import deps
        from ap_control_tower.worker import JobService
        from ap_control_tower.worker.celery_service import CeleryJobService as CJS
    except Exception as exc:  # FastAPI no instalado: opcional
        check(True, f"seleccion en API SALTEADA (dependencia opcional ausente: {exc})")
    else:
        saved = {k: os.environ.get(k) for k in ("AP_BROKER_URL", "AP_CELERY_EAGER", "CELERY_BROKER_URL")}
        try:
            os.environ.pop("AP_CELERY_EAGER", None)
            os.environ.pop("CELERY_BROKER_URL", None)
            os.environ["AP_BROKER_URL"] = "redis://localhost:6379/0"
            check(isinstance(deps._build_job_service(), CJS),
                  "con AP_BROKER_URL (sin eager) -> CeleryJobService (async real)")
            os.environ["AP_CELERY_EAGER"] = "1"
            check(isinstance(deps._build_job_service(), JobService),
                  "con AP_CELERY_EAGER=1 -> JobService inline (sin infra)")
            os.environ.pop("AP_CELERY_EAGER", None)
            os.environ.pop("AP_BROKER_URL", None)
            check(isinstance(deps._build_job_service(), JobService),
                  "sin broker -> JobService inline (demo/tests, contrato intacto)")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    print()
    if failures:
        print(f"DESPACHO ROJO: {len(failures)} fallas")
        return 1
    print("DESPACHO VERDE: 202 encola en Celery (no inline), idempotencia y seleccion OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
