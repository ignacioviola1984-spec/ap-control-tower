from __future__ import annotations

from decimal import Decimal
import json
import unittest

import httpx
from openai import OpenAI

from ap_control_tower.agent.config import AgentSettings
from ap_control_tower.agent.service import answer_question
from ap_control_tower.agent.tools import ReadOnlyDocumentTools
from ap_control_tower.audit import AuditTrail
from ap_control_tower.extraction.pdf_poc import PocResult
from ap_control_tower.ui.trial.session import TrialSession


class OpenAISDKContractTests(unittest.TestCase):
    def test_real_sdk_serializes_store_false_and_function_outputs(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            common = {
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "instructions": None,
                "max_output_tokens": 900,
                "metadata": {},
                "model": "gpt-5-mini-2025-08-07",
                "parallel_tool_calls": False,
                "temperature": 1.0,
                "tools": [],
                "top_p": 1.0,
            }
            if len(requests) == 1:
                body = {
                    **common,
                    "id": "resp_tool",
                    "output": [
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "status": "completed",
                            "arguments": "{}",
                            "call_id": "call_1",
                            "name": "get_document_context",
                        }
                    ],
                    "tool_choice": "required",
                    "usage": {
                        "input_tokens": 10,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 4,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 14,
                    },
                }
            else:
                body = {
                    **common,
                    "id": "resp_final",
                    "output": [
                        {
                            "id": "msg_1",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "La evidencia está disponible. "
                                        "La decisión final es humana."
                                    ),
                                    "annotations": [],
                                    "logprobs": [],
                                }
                            ],
                        }
                    ],
                    "tool_choice": "none",
                    "usage": {
                        "input_tokens": 20,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 8,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 28,
                    },
                }
            return httpx.Response(200, json=body)

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = OpenAI(api_key="sdk-contract-test", http_client=http_client)
        result = PocResult(
            doc_id="SDK-001",
            archivo="never-sent.pdf",
            pages=1,
            text_chars=100,
            confidence=Decimal("0.90"),
            warnings=[],
            document={
                "document_type": "invoice",
                "proveedor_nombre_comercial": "Proveedor",
                "numero_factura": "A-123",
                "fecha_emision": "2026-07-01",
                "moneda": "ARS",
                "importe_total": "100",
            },
        )
        active = TrialSession(
            audit=AuditTrail(run_id="sdk-run", commit="test"),
            results=[result],
        )

        answer = answer_question(
            "Resumí la evidencia.",
            [],
            ReadOnlyDocumentTools(active, result),
            client=client,
            settings=AgentSettings(True, "gpt-5-mini", 6, 900),
        )

        self.assertEqual(len(requests), 2)
        self.assertTrue(all(payload["store"] is False for payload in requests))
        self.assertEqual(requests[0]["tool_choice"], "required")
        self.assertEqual(requests[1]["tool_choice"], "none")
        outputs = [
            item
            for item in requests[1]["input"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual(outputs[0]["call_id"], "call_1")
        self.assertNotIn("never-sent.pdf", json.dumps(requests, ensure_ascii=False))
        self.assertIn("decisión final", answer.text.casefold())

    def test_reasoning_items_are_not_echoed_back_when_store_is_false(self):
        """gpt-5-mini devuelve items `reasoning`; con store=False la API no los
        conserva, así que reenviarlos por id hace fallar la segunda llamada."""
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            common = {
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "instructions": None,
                "max_output_tokens": 2000,
                "metadata": {},
                "model": "gpt-5-mini-2025-08-07",
                "parallel_tool_calls": False,
                "temperature": 1.0,
                "tools": [],
                "top_p": 1.0,
            }
            if len(requests) == 1:
                body = {
                    **common,
                    "id": "resp_tool",
                    "output": [
                        {"id": "rs_abc123", "type": "reasoning", "summary": []},
                        {
                            "id": "fc_1",
                            "type": "function_call",
                            "status": "completed",
                            "arguments": "{}",
                            "call_id": "call_1",
                            "name": "explain_review_reasons",
                        },
                    ],
                    "tool_choice": "required",
                    "usage": {
                        "input_tokens": 10,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 400,
                        "output_tokens_details": {"reasoning_tokens": 384},
                        "total_tokens": 410,
                    },
                }
            else:
                body = {
                    **common,
                    "id": "resp_final",
                    "output": [
                        {
                            "id": "msg_1",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Motivo registrado. La decisión es humana.",
                                    "annotations": [],
                                    "logprobs": [],
                                }
                            ],
                        }
                    ],
                    "tool_choice": "none",
                    "usage": {
                        "input_tokens": 20,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 8,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 28,
                    },
                }
            return httpx.Response(200, json=body)

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = OpenAI(api_key="sdk-contract-test", http_client=http_client)
        result = PocResult(
            doc_id="SDK-002",
            archivo="never-sent.pdf",
            pages=1,
            text_chars=100,
            confidence=Decimal("0.90"),
            warnings=[],
            document={
                "document_type": "proforma",
                "proveedor_nombre_comercial": "Proveedor",
                "numero_factura": None,
                "fecha_emision": "2026-06-05",
                "moneda": "EUR",
                "importe_total": "6050",
            },
        )
        active = TrialSession(
            audit=AuditTrail(run_id="sdk-run", commit="test"),
            results=[result],
        )

        answer = answer_question(
            "¿Por qué requiere revisión?",
            [],
            ReadOnlyDocumentTools(active, result),
            client=client,
            settings=AgentSettings(True, "gpt-5-mini", 6, 2000, "low"),
        )

        self.assertEqual(len(requests), 2)
        echoed = [item.get("type") for item in requests[1]["input"]]
        self.assertNotIn("reasoning", echoed)
        self.assertIn("function_call", echoed)
        self.assertIn("function_call_output", echoed)
        self.assertTrue(all(payload["store"] is False for payload in requests))
        self.assertTrue(
            all(payload["reasoning"] == {"effort": "low"} for payload in requests)
        )
        self.assertEqual(answer.tools_used, ("explain_review_reasons",))

    def test_reasoning_parameter_is_omitted_when_effort_is_empty(self):
        """Los modelos sin razonamiento rechazan el parámetro `reasoning`."""
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(500, json={"error": {"message": "corte"}})

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = OpenAI(
            api_key="sdk-contract-test", http_client=http_client, max_retries=0
        )
        result = PocResult(
            doc_id="SDK-003",
            archivo="never-sent.pdf",
            pages=1,
            text_chars=10,
            confidence=Decimal("0.90"),
            warnings=[],
            document={"document_type": "invoice"},
        )
        active = TrialSession(
            audit=AuditTrail(run_id="sdk-run", commit="test"),
            results=[result],
        )

        with self.assertRaises(Exception):
            answer_question(
                "Resumí la evidencia.",
                [],
                ReadOnlyDocumentTools(active, result),
                client=client,
                settings=AgentSettings(True, "gpt-4.1-mini", 6, 2000, ""),
            )

        self.assertEqual(len(requests), 1)
        self.assertNotIn("reasoning", requests[0])


if __name__ == "__main__":
    unittest.main()
