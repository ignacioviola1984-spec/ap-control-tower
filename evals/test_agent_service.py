from __future__ import annotations

from decimal import Decimal
import json
from types import SimpleNamespace
import unittest

from ap_control_tower.agent.config import AgentSettings
from ap_control_tower.agent.service import answer_question, audit_answer
from ap_control_tower.agent.tools import ReadOnlyDocumentTools
from ap_control_tower.audit import AuditTrail
from ap_control_tower.extraction.pdf_poc import PocResult
from ap_control_tower.ui.trial.session import TrialSession


class _FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="explain_review_reasons",
                        arguments="{}",
                        call_id="call-1",
                    )
                ],
                usage=SimpleNamespace(input_tokens=120, output_tokens=20),
            )
        return SimpleNamespace(
            output=[],
            output_text=(
                "El documento requiere revisión por un campo crítico faltante. "
                "La decisión final corresponde a una persona revisora."
            ),
            usage=SimpleNamespace(input_tokens=220, output_tokens=45),
        )


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()


def _fixture() -> tuple[TrialSession, PocResult]:
    result = PocResult(
        doc_id="DOC-AGENT",
        archivo="no-se-envia.pdf",
        pages=1,
        text_chars=900,
        confidence=Decimal("0.75"),
        warnings=[],
        document={
            "document_type": "invoice",
            "proveedor_nombre_comercial": "Proveedor",
            "proveedor_tax_id": "30712345678",
            "numero_factura": None,
            "fecha_emision": "2026-07-20",
            "moneda": "ARS",
            "importe_total": "1000.00",
            "iban": "ES9121000418450200051332",
        },
    )
    active = TrialSession(
        audit=AuditTrail(run_id="run-agent-service", commit="test"),
        results=[result],
    )
    return active, result


class AgentServiceTests(unittest.TestCase):
    def test_responses_flow_uses_local_tool_and_store_false(self):
        active, result = _fixture()
        client = _FakeClient()
        settings = AgentSettings(
            enabled=True,
            model="gpt-5-mini",
            max_history_messages=6,
            max_output_tokens=900,
        )
        answer = answer_question(
            "¿Por qué requiere revisión?",
            [],
            ReadOnlyDocumentTools(active, result),
            client=client,
            settings=settings,
        )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertTrue(all(call["store"] is False for call in client.responses.calls))
        self.assertEqual(client.responses.calls[0]["tool_choice"], "required")
        self.assertEqual(client.responses.calls[1]["tool_choice"], "none")
        self.assertEqual(answer.tools_used, ("explain_review_reasons",))
        self.assertEqual(answer.input_tokens, 340)
        self.assertEqual(answer.output_tokens, 65)

        second_input = client.responses.calls[1]["input"]
        tool_outputs = [
            item for item in second_input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        self.assertEqual(len(tool_outputs), 1)
        payload = json.loads(tool_outputs[0]["output"])
        self.assertTrue(payload["requiere_revision"])

    def test_audit_contains_metadata_but_not_prompt_or_response(self):
        active, result = _fixture()
        client = _FakeClient()
        answer = answer_question(
            "Consulta secreta que no debe auditarse",
            [],
            ReadOnlyDocumentTools(active, result),
            client=client,
            settings=AgentSettings(True, "gpt-5-mini", 6, 900),
        )
        audit_answer(active, result.doc_id, answer)
        event = active.audit.events[-1]
        serialized = json.dumps(event.evidence, ensure_ascii=False)

        self.assertEqual(event.action, "consulta-asistente-ap")
        self.assertEqual(event.result, "respondida")
        self.assertNotIn("Consulta secreta", serialized)
        self.assertNotIn(answer.text, serialized)
        self.assertFalse(event.evidence["store"])
        self.assertFalse(event.evidence["pdf_enviado"])
        self.assertTrue(event.evidence["solo_lectura"])
        self.assertTrue(active.audit.verify_chain())


if __name__ == "__main__":
    unittest.main()
