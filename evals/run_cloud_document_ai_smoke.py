"""Smoke test de Cloud Run Job -> Document AI, sin datos del cliente.

Genera una factura sintetica en memoria y exige que el Invoice Parser recupere
numero y total. Usa ADC local o la identidad del metadata server en Cloud Run.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_control_tower.extraction.document_ai import (
    DocumentAIConfig,
    process_invoice_bytes,
)


EXPECTED_NUMBER = "SMOKE-2026-001"
EXPECTED_TOTAL = "121.00"


def _synthetic_invoice() -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    lines = (
        "DEMO SUPPLIER S.L.",
        "Tax ID: B12345678",
        "INVOICE",
        f"Invoice number: {EXPECTED_NUMBER}",
        "Invoice date: 2026-07-11",
        "Bill to: Demo Customer S.L.",
        "Net amount: EUR 100.00",
        "VAT 21%: EUR 21.00",
        "Total amount: EUR 121.00",
    )
    y = 790
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 28
    pdf.save()
    return buffer.getvalue()


def main() -> int:
    config = DocumentAIConfig.from_env()
    if config is None:
        raise RuntimeError("Document AI no esta configurado")
    result = process_invoice_bytes("cloud-run-smoke.pdf", _synthetic_invoice(), config)
    number = result.document.get("numero_factura")
    total = result.document.get("importe_total")
    if number != EXPECTED_NUMBER or total != EXPECTED_TOTAL:
        raise AssertionError(
            f"extraccion inesperada: numero={number!r}, total={total!r}"
        )
    print(f"OK engine={result.engine} invoice={number} total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
