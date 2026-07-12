"""Regresión del workflow real: revisión humana + propuesta de pago."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@dataclass
class FakeResult:
    doc_id: str
    document: dict
    confidence: Decimal = Decimal("0.90")
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)
    engine: str = "google_document_ai_invoice_parser"
    pages: int = 1
    text_chars: int = 100


def invoice(doc_id: str, *, po=None, confidence="0.95") -> FakeResult:
    return FakeResult(doc_id, {
        "document_type": "invoice",
        "proveedor_nombre_comercial": "Proveedor SL",
        "numero_factura": doc_id,
        "fecha_emision": "2026-05-01",
        "fecha_vencimiento_calculada": "2026-05-31",
        "moneda": "EUR",
        "importe_total": "100.00",
        "po_reference": po,
    }, field_confidences={"numero_factura": Decimal(confidence),
                          "importe_total": Decimal("0.95")})


def must_raise(fn, text: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError(f"se esperaba ValueError: {text}")


def main() -> int:
    from ap_control_tower.ui.trial import session as sess
    from ap_control_tower.ui.trial import workflow

    print("== Enrutamiento: OC ausente no implica revisión ==")
    clean = invoice("F-1", po=None)
    assert workflow.review_reasons(clean) == []
    assert workflow.approval_state(clean, {}, {})["status"] == "eligible"
    print("  PASS  factura non-PO limpia es elegible sin revisión")

    print("== Baja confianza y campos críticos ==")
    low = invoice("F-2", confidence="0.50")
    assert any("baja confianza" in reason for reason in workflow.review_reasons(low))
    missing = invoice("F-3")
    missing.document["importe_total"] = None
    assert "importe_total" in workflow.missing_critical_fields(missing.document)
    assert workflow.approval_state(missing, {}, {})["status"] == "retained"
    print("  PASS  baja confianza y campos ausentes se retienen")

    print("== Proforma nunca llega a propuesta de pago ==")
    proforma = invoice("P-1")
    proforma.document["document_type"] = "proforma_or_advance_request"
    state = workflow.approval_state(proforma, {}, {})
    assert state["status"] == "retained" and "no es una factura fiscal" in state["reasons"]
    print("  PASS  proforma retenida")

    print("== Duplicados ==")
    duplicate = invoice("F-1-COPY")
    duplicate.document["numero_factura"] = "F-1"
    ids = workflow.duplicate_doc_ids([clean, duplicate])
    assert ids == {"F-1", "F-1-COPY"}
    print("  PASS  mismo proveedor+número detectado")

    print("== Decisión humana, maker-checker y no liberación ==")
    active = sess.new_session()
    active.results = [low, clean, proforma]
    sess.confirm_review(active, "F-2", "Ana Revisora", {
        field: str(low.document.get(field) or "") for field in workflow.EDITABLE_FIELDS
    }, "validado contra factura")
    assert active.review_decisions["F-2"]["status"] == "confirmed"
    assert workflow.approval_state(
        low, active.review_decisions, active.approval_decisions)["status"] == "eligible"
    must_raise(lambda: sess.decide_payment_proposal(
        active, ["F-2"], "Ana Revisora", "approved"), "Maker-checker")
    sess.decide_payment_proposal(active, ["F-2", "F-1"], "Bruno Aprobador",
                                 "approved", "lote propuesto")
    assert active.approval_decisions["F-2"]["status"] == "approved"
    last = active.audit.events[-1]
    assert last.action == "aprobada-para-propuesta-pago"
    assert last.evidence["no_libera_dinero"] is True
    must_raise(lambda: sess.decide_payment_proposal(
        active, ["P-1"], "Bruno Aprobador", "approved"), "no es elegible")
    print("  PASS  maker-checker, auditoría y proforma bloqueada")

    print("== Retención/rechazo requiere motivo ==")
    must_raise(lambda: sess.retain_review(active, "F-1", "Ana", ""), "motivo")
    must_raise(lambda: sess.decide_payment_proposal(
        active, ["P-1"], "Bruno", "rejected", ""), "motivo")
    print("  PASS  decisiones negativas justificadas")

    assert active.audit.verify_chain()
    print("\nTRIAL WORKFLOW VERDE: revisión, maker-checker, propuesta y audit trail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
