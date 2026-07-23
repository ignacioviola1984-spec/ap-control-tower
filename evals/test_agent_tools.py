from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import unittest

from ap_control_tower.agent.privacy import redact_text, safe_document_fields
from ap_control_tower.agent.tools import ReadOnlyDocumentTools
from ap_control_tower.audit import AuditTrail
from ap_control_tower.extraction.pdf_poc import PocResult
from ap_control_tower.ui.trial.session import TrialSession


def _session_and_result() -> tuple[TrialSession, PocResult]:
    audit = AuditTrail(run_id="run-agent-test", commit="test")
    result = PocResult(
        doc_id="DOC-001",
        archivo="factura-real.pdf",
        pages=2,
        text_chars=2400,
        confidence=Decimal("0.82"),
        warnings=[
            "Baja confianza en: proveedor_tax_id",
            "Cuenta detectada ES9121000418450200051332",
        ],
        document={
            "document_type": "invoice",
            "proveedor_nombre_comercial": "Proveedor Seguro SA",
            "proveedor_tax_id": "30712345678",
            "numero_factura": "FAC-2026-00012345",
            "fecha_emision": "2026-07-20",
            "fecha_vencimiento_calculada": "2026-08-19",
            "moneda": "ARS",
            "importe_total": "150000.00",
            "importe_neto": "123966.94",
            "importe_iva": "26033.06",
            "po_reference": "OC-99887766",
            "iban": "ES9121000418450200051332",
        },
        engine="google_document_ai_invoice_parser",
        field_confidences={"proveedor_tax_id": Decimal("0.40")},
    )
    session = TrialSession(audit=audit, results=[result])
    audit.add(
        agent="sistema",
        action="documento-procesado",
        invoice_id=result.doc_id,
        result="con-advertencias",
        evidence={"advertencias": 2},
    )
    return session, result


class AgentPrivacyTests(unittest.TestCase):
    def test_safe_fields_mask_sensitive_identifiers(self):
        _, result = _session_and_result()
        safe = safe_document_fields(result.document)
        serialized = json.dumps(safe, ensure_ascii=False)
        self.assertNotIn("30712345678", serialized)
        self.assertNotIn("ES9121000418450200051332", serialized)
        self.assertNotIn("FAC-2026-00012345", serialized)
        self.assertNotIn("OC-99887766", serialized)
        self.assertIn("****678", safe["id_fiscal_proveedor"])
        self.assertTrue(safe["datos_bancarios_presentes"])

    def test_user_text_redacts_long_numbers_and_iban(self):
        redacted = redact_text(
            "Mi CUIT es 30712345678 y el IBAN ES9121000418450200051332."
        )
        self.assertNotIn("30712345678", redacted)
        self.assertNotIn("ES9121000418450200051332", redacted)
        self.assertIn("5678", redacted)
        self.assertIn("1332", redacted)


class ReadOnlyDocumentToolTests(unittest.TestCase):
    def test_tools_do_not_mutate_document_or_decisions(self):
        active, result = _session_and_result()
        before_document = deepcopy(result.document)
        before_review = deepcopy(active.review_decisions)
        before_approval = deepcopy(active.approval_decisions)
        toolbox = ReadOnlyDocumentTools(active, result)

        for name in (
            "get_document_context",
            "explain_review_reasons",
            "summarize_document_evidence",
            "suggest_reviewer_actions",
            "get_vendor_master_status",
        ):
            payload = json.loads(toolbox.dispatch(name, {}))
            self.assertIsInstance(payload, dict)

        self.assertEqual(result.document, before_document)
        self.assertEqual(active.review_decisions, before_review)
        self.assertEqual(active.approval_decisions, before_approval)

    def test_vendor_master_unavailable_is_explicit(self):
        active, result = _session_and_result()
        payload = ReadOnlyDocumentTools(active, result).get_vendor_master_status()
        self.assertEqual(payload["estado_maestro"], "no_disponible")
        self.assertEqual(payload["estado_vinculacion"], "no_evaluable")

    def test_evidence_never_contains_pdf_or_full_bank_data(self):
        active, result = _session_and_result()
        toolbox = ReadOnlyDocumentTools(active, result)
        serialized = json.dumps(
            {
                "context": toolbox.get_document_context(),
                "evidence": toolbox.summarize_document_evidence(),
            },
            ensure_ascii=False,
        )
        self.assertNotIn("factura-real.pdf", serialized)
        self.assertNotIn("ES9121000418450200051332", serialized)
        self.assertFalse(toolbox.summarize_document_evidence()["pdf_enviado_al_modelo"])


if __name__ == "__main__":
    unittest.main()
