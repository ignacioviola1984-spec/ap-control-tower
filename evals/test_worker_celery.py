"""Evals del transporte Celery (Fase 5) en modo EAGER (sin broker/Redis).

Verifica que las tareas Celery ejecutan las mismas funciones nucleo y que la
politica de reintentos nativa deja una tarea fallida en FAILURE (dead-letter
del backend). SKIP con exit 0 si Celery no esta instalado.
"""

from __future__ import annotations

import base64
import os
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def _tiny_pdf() -> bytes:
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "FACTURA Nro F-2026/777")
    c.drawString(100, 730, "Proveedor Demo SL - CIF ESB12345678")
    c.drawString(100, 710, "Total: 1210,00 EUR")
    c.save()
    return buf.getvalue()


def main() -> int:
    try:
        import celery  # noqa: F401
    except Exception:
        print("== Celery: SALTEADO (no instalado) ==")
        print("  SKIP  instalar con: pip install -r requirements-worker.txt")
        return 0

    # eager ANTES de construir la app Celery
    os.environ["AP_CELERY_EAGER"] = "1"
    os.environ.pop("AP_BROKER_URL", None)

    from ap_control_tower.worker.celery_app import celery_app
    from ap_control_tower.worker.tasks import process_document_task

    print("== 1. La app Celery esta en modo eager (sin infra) ==")
    check(celery_app.conf.task_always_eager is True,
          "task_always_eager activo (corre sin broker)")
    check(celery_app.conf.task_acks_late is True
          and celery_app.conf.task_soft_time_limit == 90,
          "config de robustez: acks_late + soft_time_limit")

    print("== 2. Tarea de documento ejecuta el nucleo y enmascara ==")
    b64 = base64.b64encode(_tiny_pdf()).decode()
    res = process_document_task.delay("factura.pdf", b64)
    out = res.get(timeout=30)
    check(isinstance(out, dict) and "document" in out,
          f"la tarea procesa el documento (motor: {out.get('motor')})")
    doc = out["document"]
    check(doc.get("iban") is None or "*" in str(doc.get("iban")),
          "el IBAN (si aparece) viaja enmascarado desde el worker")

    print("== 3. Documento invalido: reintenta y termina en FAILURE ==")
    bad = base64.b64encode(b"esto no es un PDF").decode()
    res2 = process_document_task.delay("roto.pdf", bad)
    failed = False
    try:
        res2.get(timeout=30, propagate=True)
    except Exception:
        failed = True
    check(failed or res2.failed(),
          "un documento invalido agota reintentos y queda en FAILURE (dead-letter)")

    print()
    if failures:
        print(f"CELERY ROJO: {len(failures)} fallas")
        return 1
    print("CELERY VERDE: transporte de tareas en eager OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
