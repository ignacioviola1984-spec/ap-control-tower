from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ap_control_tower.evidence_memory import HistoricalEvidenceMemory
from ap_control_tower.extraction.pdf_poc import PocResult


def _result(document: dict) -> PocResult:
    return PocResult(
        doc_id="test", archivo="test.pdf", pages=1, text_chars=100,
        confidence=Decimal("0.80"), warnings=[], document=document,
    )


class HistoricalEvidenceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "memory.sqlite3"
        connection = sqlite3.connect(self.db_path)
        connection.executescript("""
            CREATE TABLE documents (
                document_sha256 TEXT PRIMARY KEY,
                corpus_document_id TEXT,
                supplier_tax_id TEXT,
                supplier_name TEXT
            );
            CREATE TABLE field_evidence (
                document_sha256 TEXT,
                field_name TEXT,
                value TEXT,
                page_number INTEGER,
                confidence REAL,
                review_status TEXT
            );
        """)
        exact_data = b"exact-pdf"
        digest = hashlib.sha256(exact_data).hexdigest()
        connection.execute(
            "INSERT INTO documents VALUES (?,?,?,?)",
            (digest, "EV-CAL-X", "ESX0000000X", "Proveedor Memoria SL"),
        )
        rows = [
            (digest, "proveedor_nombre_comercial", "Proveedor Memoria", 1, 1.0, "verified_ground_truth"),
            (digest, "proveedor_registro", "KVK 12345678", 1, 1.0, "verified_ground_truth"),
            (digest, "periodo_servicio_desde", "2026-01-01", 1, 1.0, "verified_ground_truth"),
            (digest, "periodo_servicio_hasta", "2026-01-31", 1, 1.0, "verified_ground_truth"),
            (digest, "condiciones_pago", "", None, 1.0, "verified_absent"),
        ]
        connection.executemany("INSERT INTO field_evidence VALUES (?,?,?,?,?,?)", rows)
        connection.commit()
        connection.close()
        self.exact_data = exact_data

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_document_uses_verified_values_and_absences(self):
        result = _result({
            "proveedor_nombre_comercial": "OCR incorrecto",
            "proveedor_registro": None,
            "periodo_servicio_desde": None,
            "periodo_servicio_hasta": None,
            "condiciones_pago": "texto falso",
        })
        applied = HistoricalEvidenceMemory(self.db_path).enrich_result(result, self.exact_data)
        self.assertEqual(result.document["proveedor_nombre_comercial"], "Proveedor Memoria")
        self.assertEqual(result.document["proveedor_registro"], "KVK 12345678")
        self.assertEqual(result.document["periodo_servicio_desde"], "2026-01-01")
        self.assertEqual(result.document["periodo_servicio_hasta"], "2026-01-31")
        self.assertIsNone(result.document["condiciones_pago"])
        self.assertEqual(len(applied), 5)
        self.assertTrue(all("FYI memoria histórica" in warning for warning in result.warnings))

    def test_new_document_only_inherits_unique_verified_registry(self):
        result = _result({
            "proveedor_nombre_comercial": "Proveedor Memoria SL",
            "proveedor_tax_id": "X0000000X",
            "proveedor_registro": None,
            "periodo_servicio_desde": None,
            "periodo_servicio_hasta": None,
            "condiciones_pago": None,
        })
        HistoricalEvidenceMemory(self.db_path).enrich_result(result, b"new-pdf")
        self.assertEqual(result.document["proveedor_registro"], "KVK 12345678")
        self.assertIsNone(result.document["periodo_servicio_desde"])
        self.assertIsNone(result.document["condiciones_pago"])

    def test_model_corroborated_fills_only_the_exact_document(self):
        exact_data = b"model-corroborated-pdf"
        digest = hashlib.sha256(exact_data).hexdigest()
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            "INSERT INTO documents VALUES (?,?,?,?)",
            (digest, "Q1Q2-MODEL", "ESY0000000Y", "Proveedor Modelo SL"),
        )
        connection.executemany(
            "INSERT INTO field_evidence VALUES (?,?,?,?,?,?)",
            [
                (digest, "proveedor_registro", "HRB 98765", 1, 0.99, "model_corroborated"),
                (digest, "periodo_servicio_desde", "2026-02-01", 1, 0.96, "model_corroborated"),
                (digest, "periodo_servicio_hasta", "2026-02-28", 1, 0.96, "model_corroborated"),
                (digest, "condiciones_pago", "30 days", 1, 0.95, "model_corroborated"),
            ],
        )
        connection.commit()
        connection.close()

        exact_result = _result({
            "proveedor_registro": None,
            "periodo_servicio_desde": None,
            "periodo_servicio_hasta": None,
            "condiciones_pago": "valor ya extraído",
        })
        applied = HistoricalEvidenceMemory(self.db_path).enrich_result(exact_result, exact_data)
        self.assertEqual(exact_result.document["proveedor_registro"], "HRB 98765")
        self.assertEqual(exact_result.document["periodo_servicio_desde"], "2026-02-01")
        self.assertEqual(exact_result.document["periodo_servicio_hasta"], "2026-02-28")
        self.assertEqual(exact_result.document["condiciones_pago"], "valor ya extraído")
        self.assertEqual(len(applied), 3)
        self.assertTrue(all(item.action == "filled_from_model_corroborated_evidence" for item in applied))

        other_result = _result({
            "proveedor_nombre_comercial": "Proveedor Modelo SL",
            "proveedor_tax_id": "Y0000000Y",
            "proveedor_registro": None,
            "periodo_servicio_desde": None,
            "periodo_servicio_hasta": None,
            "condiciones_pago": None,
        })
        HistoricalEvidenceMemory(self.db_path).enrich_result(other_result, b"different-pdf")
        self.assertIsNone(other_result.document["proveedor_registro"])
        self.assertIsNone(other_result.document["periodo_servicio_desde"])
        self.assertIsNone(other_result.document["condiciones_pago"])


if __name__ == "__main__":
    unittest.main()
