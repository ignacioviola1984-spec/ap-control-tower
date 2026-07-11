"""Eval hermetica del caso de negocio session-only del trial."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


@dataclass
class FakeResult:
    doc_id: str
    document: dict
    engine: str
    confidence: Decimal = Decimal("0.90")
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)


def result(doc_id: str, engine: str, po: bool = False, warning: bool = False) -> FakeResult:
    from ap_control_tower.extraction.schema import empty_document

    doc = empty_document()
    doc.update({
        "document_type": "invoice",
        "proveedor_nombre_comercial": "Proveedor Test SL",
        "numero_factura": f"F-{doc_id}",
        "fecha_emision": "2026-07-01",
        "importe_total": "121.00",
        "moneda": "EUR",
        "po_reference": "PO-1" if po else None,
    })
    conf = {
        field: Decimal("0.90")
        for field in (
            "proveedor_nombre_comercial", "numero_factura", "fecha_emision",
            "importe_total", "moneda",
        )
    }
    return FakeResult(
        doc_id=doc_id,
        document=doc,
        engine=engine,
        warnings=["campo crítico ausente"] if warning else [],
        field_confidences=conf,
    )


def main() -> int:
    from ap_control_tower.ui.trial import business_case as bc

    print("== Elegibilidad: solo Google Document AI ==")
    managed = result("M1", bc.MANAGED_ENGINE)
    local = result("L1", "fallback_local")
    check(bc.managed_results([managed, local]) == [managed],
          "el motor local queda excluido del caso de negocio")

    print("== Metricas basadas en evidencia de la sesion ==")
    managed_po = result("M2", bc.MANAGED_ENGINE, po=True, warning=True)
    metrics = bc.calculate_metrics(
        [managed, local, managed_po], {"M1": 1.0, "L1": 99.0, "M2": 3.0}
    )
    check(metrics.documents == 2 and metrics.invoices == 2,
          "cuenta solo documentos y facturas del parser administrado")
    check(metrics.critical_found == 10 and metrics.critical_possible == 10,
          "cobertura calculada sobre cinco campos AS-IS medibles")
    check(abs(metrics.coverage - 1.0) < 1e-9,
          "cobertura de campos críticos = 100%")
    check(metrics.confidence is not None and abs(metrics.confidence - 0.9) < 1e-9,
          "confianza promedio usa confianzas por campo informadas")
    check(metrics.clean_documents == 1 and metrics.review_documents == 1,
          "separa documentos limpios y a revisar")
    check(metrics.po_documents == 1 and metrics.non_po_documents == 1,
          "sin OC es ruta non-PO normal")
    check(abs(metrics.total_seconds - 4.0) < 1e-9 and abs(metrics.average_seconds - 2.0) < 1e-9,
          "tiempos excluyen resultados del motor local")

    rows = bc.field_coverage_rows([managed, local, managed_po])
    check(len(rows) == 5 and all(r["Cobertura"] == "100%" for r in rows),
          "tabla de cobertura por campo usa solo Google Document AI")

    print()
    if failures:
        print(f"TRIAL BUSINESS CASE ROJO: {len(failures)} fallas")
        return 1
    print("TRIAL BUSINESS CASE VERDE: evidencia Google-only, AS-IS y non-PO (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
