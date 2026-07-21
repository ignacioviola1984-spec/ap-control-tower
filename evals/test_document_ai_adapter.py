from __future__ import annotations

import os
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ap_control_tower.extraction.banking import (
    extract_bank_details,
    is_valid_bic,
    is_valid_iban,
    is_valid_spanish_ccc,
)
from ap_control_tower.extraction.document_ai import (
    DocumentAIConfig,
    extract_uploaded_document,
    map_document_ai_result,
)
from ap_control_tower.extraction.pdf_poc import PdfText, PocResult, extract_document
from ap_control_tower.extraction.schema import empty_document


def entity(entity_type: str, mention: str, normalized: str | None = None, confidence: float = 0.95):
    normalized_value = SimpleNamespace(text=normalized) if normalized is not None else None
    return SimpleNamespace(
        type_=entity_type,
        mention_text=mention,
        normalized_value=normalized_value,
        confidence=confidence,
        properties=[],
    )


class DocumentAIAdapterTests(unittest.TestCase):
    def test_maps_invoice_layout_and_banking_fields(self):
        text = """Aurora, Soluciones Digitales para Empresas S.L.L.
CIF B12345674
CLIENTE: Meridia Consulting S.L.U. - CIF B00000000
FACTURA F100
FECHA: 06/05/2026
BASE IMPONIBLE EUR 100,00
IVA 21% EUR 21,00
TOTAL EUR 121,00
DETALLES DE PAGO
CUENTA
2100-0418-45-0200051332 (BANCO AURORA)
IBAN ES91 2100 0418 4502 0005 1332
BIC CAIXESBBXXX
"""
        document = SimpleNamespace(
            text=text,
            pages=[object()],
            entities=[
                entity("invoice_id", "F100"),
                entity("invoice_date", "06/05/2026", "2026-05-06"),
                entity("supplier_name", "Soluciones Digitales, S.L.L.", confidence=0.78),
                entity("supplier_tax_id", "B12345674"),
                entity("receiver_name", "Meridia Consulting S.L.U."),
                entity("receiver_tax_id", "B00000000"),
                entity("currency", "€", "EUR"),
                entity("net_amount", "100,00", "100.00"),
                entity("total_tax_amount", "21,00", "21.00"),
                entity("total_amount", "121,00", "121.00"),
                entity("supplier_iban", "ES91 2100 0418 4502 0005 1332"),
            ],
        )

        result = map_document_ai_result("factura.pdf", document)
        doc = result.document

        self.assertEqual(doc["proveedor_nombre_comercial"], "Aurora, Soluciones Digitales para Empresas S.L.L")
        self.assertEqual(doc["cliente_nombre"], "Meridia Consulting S.L.U.")
        self.assertEqual(doc["fecha_emision"], "2026-05-06")
        self.assertEqual(doc["tipo_iva"], "21")
        self.assertEqual(doc["importe_iva"], "21.00")
        self.assertEqual(doc["importe_total"], "121.00")
        self.assertEqual(doc["proveedor_banco"], "BANCO AURORA")
        self.assertEqual(doc["proveedor_cuenta_bancaria"], "2100-0418-45-0200051332")
        self.assertEqual(doc["iban"], "ES9121000418450200051332")
        self.assertEqual(doc["bic"], "CAIXESBBXXX")
        self.assertEqual(doc["metodo_pago"], "transferencia")
        self.assertEqual(doc["tratamiento_iva"], "nacional")
        self.assertEqual(result.warnings, [])

    def test_reverse_charge_takes_precedence_over_zero_tax(self):
        text = """Panel Europe BV
Bill to: Meridia Consulting SL
INVOICE PB-1
Invoice date: 12 June 2026
Net amount EUR 4200.00
VAT 0% EUR 0.00
TOTAL EUR 4200.00
VAT reverse-charged under article 196.
"""
        document = SimpleNamespace(
            text=text,
            pages=[object()],
            entities=[
                entity("invoice_id", "PB-1"),
                entity("invoice_date", "12 June 2026", "2026-06-12"),
                entity("supplier_name", "Panel Europe BV"),
                entity("receiver_name", "Meridia Consulting SL"),
                entity("currency", "EUR", "EUR"),
                entity("net_amount", "4200.00", "4200.00"),
                entity("total_tax_amount", "0.00", "0.00"),
                entity("total_amount", "4200.00", "4200.00"),
                entity("vat/tax_rate", "0%", "0"),
            ],
        )

        result = map_document_ai_result("reverse-charge.pdf", document)

        self.assertEqual(result.document["tipo_iva"], "0")
        self.assertEqual(
            result.document["tratamiento_iva"],
            "intracomunitario_inversion_sujeto_pasivo",
        )

    def test_iban_country_corrects_inverted_supplier_and_receiver(self):
        text = """SASU RESEARCH PARTNER
12 RUE EXAMPLE
75001 PARIS
FR
MERIDIA CONSULTING S.L.U.
Madrid
VAT Reg No: ESB00000000
Invoice N° 200
Date of issue: 05/05/2026
VAT
0%
Total before tax EUR 4200.00
Total including VAT EUR 4200.00
IBAN: FR1420041010050500013M02606
BIC: PSSTFRPPMON
Reverse charge
TVA intracommunautaire: FR40303265045
"""
        document = SimpleNamespace(
            text=text,
            pages=[object()],
            entities=[
                entity("invoice_id", "200"),
                entity("invoice_date", "05/05/2026", "2026-05-05"),
                entity("supplier_name", "MERIDIA CONSULTING S.L.U."),
                entity("supplier_tax_id", "ESB00000000"),
                entity("currency", "EUR", "EUR"),
                entity("net_amount", "4200.00", "4200.00"),
                entity("total_amount", "4200.00", "4200.00"),
                entity("supplier_iban", "FR1420041010050500013M02606"),
            ],
        )

        result = map_document_ai_result("inverted.pdf", document)
        doc = result.document

        self.assertEqual(doc["proveedor_nombre_comercial"], "SASU RESEARCH PARTNER")
        self.assertEqual(doc["proveedor_tax_id"], "FR40303265045")
        self.assertEqual(doc["cliente_nombre"], "MERIDIA CONSULTING S.L.U")
        self.assertEqual(doc["cliente_tax_id"], "ESB00000000")
        self.assertEqual(doc["tipo_iva"], "0")
        self.assertEqual(doc["importe_iva"], "0.00")

    def test_tax_math_disambiguates_discount_from_vat_rate(self):
        text = """SERVICIOS VERDES
MERIDIA CONSULTING SLU
NIF/CIF: ESB00000000
Factura FV-1
Fecha 05/05/2026
DTO 0,00% IVA 21%
Base imponible 36,68 EUR
Impuesto 7,70 EUR
Total 44,38 EUR
Numero de cuenta: ****************107 3717
SERVICIOS VERDES UNA TINTA S.L.U. N.I.F: B12345674
"""
        document = SimpleNamespace(
            text=text,
            pages=[object()],
            entities=[
                entity("invoice_id", "FV-1"),
                entity("invoice_date", "05/05/2026", "2026-05-05"),
                entity("supplier_name", "grupo"),
                entity("supplier_tax_id", "B12345674"),
                entity("currency", "EUR", "EUR"),
                entity("net_amount", "36,68", "36.68"),
                entity("total_tax_amount", "7,70", "7.70"),
                entity("total_amount", "44,38", "44.38"),
            ],
        )

        result = map_document_ai_result("discount-and-vat.pdf", document)

        self.assertEqual(result.document["tipo_iva"], "21")
        self.assertEqual(result.document["proveedor_nombre_comercial"], "SERVICIOS VERDES UNA TINTA S.L.U")
        self.assertEqual(result.document["cliente_nombre"], "MERIDIA CONSULTING SLU")

    def test_banking_validators_reject_plausible_but_invalid_values(self):
        self.assertTrue(is_valid_iban("ES9121000418450200051332"))
        self.assertFalse(is_valid_iban("ES4321000418450200051332"))
        self.assertTrue(is_valid_bic("CAIXESBBXXX"))
        self.assertFalse(is_valid_bic("CAIXZZBBXXX"))
        self.assertTrue(is_valid_spanish_ccc("2100-0418-45-0200051332"))
        self.assertFalse(is_valid_spanish_ccc("2100-0418-44-0200051332"))

    def test_extracts_masked_account_without_inventing_digits(self):
        details = extract_bank_details("Numero de cuenta: ****************107 3717")
        self.assertEqual(details.cuenta, "****************107 3717")
        self.assertIsNone(details.iban)

    def test_config_requires_project_and_processor(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(DocumentAIConfig.from_env())
        with patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "demo-project",
            "DOCUMENT_AI_PROCESSOR_ID": "processor-1",
        }, clear=True):
            config = DocumentAIConfig.from_env()
        self.assertEqual(config, DocumentAIConfig("demo-project", "us", "processor-1"))

    def test_unconfigured_invoice_degrades_to_reviewable_local_result(self):
        document = empty_document()
        document.update({"document_type": "invoice", "numero_factura": "F-1"})
        local = PocResult(
            doc_id="factura",
            archivo="factura.pdf",
            pages=1,
            text_chars=10,
            confidence=Decimal("0.88"),
            warnings=[],
            document=document,
        )
        pdf = PdfText(path=Path("factura.pdf"), pages=1, text="FACTURA F-1")
        with patch.dict(os.environ, {}, clear=True), \
                patch("ap_control_tower.extraction.document_ai.read_pdf_bytes", return_value=pdf), \
                patch("ap_control_tower.extraction.document_ai.extract_document", return_value=local):
            result = extract_uploaded_document("factura.pdf", b"pdf")
        self.assertEqual(result.engine, "fallback_local")
        self.assertLessEqual(result.confidence, Decimal("0.49"))
        self.assertIn("no configurado", result.warnings[0].casefold())

    def test_local_oc_preserves_spaced_thousands_and_avoids_fake_tax_ids(self):
        pdf = PdfText(
            path=Path("Orden de Compra EURE 1583.pdf"),
            pages=1,
            text=(
                "ORDEN DE COMPRA EURE 1583\nProveedor Nombre: GESMAR\n"
                "Incentivos & Referentes 1 .320,00 EUR\n"
                "Total 1 .320,00 EUR\nDesgrabaciones & Traducciones\n"
                "En caso de varias imputaciones se deberan desagregar."
            ),
        )
        doc = extract_document(pdf).document
        self.assertEqual(doc["importe_total"], "1320.00")
        self.assertEqual(doc["po_reference"], "EURE 1583")
        self.assertEqual(doc["proveedor_nombre_comercial"], "GESMAR")
        self.assertIsNone(doc["proveedor_tax_id"])
        self.assertIsNone(doc["cliente_tax_id"])

    def test_contract_project_reference_is_not_promoted_to_po(self):
        pdf = PdfText(
            path=Path("dynata.pdf"),
            pages=1,
            text=(
                "Factura\nNumero de factura: ES01-ARIV-0010205\n"
                "Contrato del proyecto: ORD-1796742-G6C4-ES01\n"
                "Base imponible: 17.410,00 EUR\nIVA 21%: 3.656,10 EUR\n"
                "Total general: 21.066,10 EUR"
            ),
        )
        doc = extract_document(pdf).document
        self.assertEqual(doc["project_reference"], "ORD-1796742-G6C4-ES01")
        self.assertIsNone(doc["po_reference"])

    def test_managed_invoice_keeps_visible_local_reference_when_ocr_omits_it(self):
        local_doc = empty_document()
        local_doc.update({
            "document_type": "invoice",
            "numero_factura": "ES01-ARIV-0010205",
            "project_reference": "ORD-1796742-G6C4-ES01",
        })
        local = PocResult(
            doc_id="dynata",
            archivo="dynata.pdf",
            pages=1,
            text_chars=80,
            confidence=Decimal("0.70"),
            warnings=[],
            document=local_doc,
        )
        managed_doc = empty_document()
        managed_doc.update({
            "document_type": "invoice",
            "numero_factura": "ES01-ARIV-0010205",
        })
        managed = PocResult(
            doc_id="dynata",
            archivo="dynata.pdf",
            pages=1,
            text_chars=80,
            confidence=Decimal("0.90"),
            warnings=[],
            document=managed_doc,
            engine="google_document_ai_invoice_parser",
        )
        pdf = PdfText(path=Path("dynata.pdf"), pages=1, text="Factura")
        with patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "demo-project",
            "DOCUMENT_AI_PROCESSOR_ID": "processor-1",
        }, clear=True), \
                patch("ap_control_tower.extraction.document_ai.read_pdf_bytes", return_value=pdf), \
                patch("ap_control_tower.extraction.document_ai.extract_document", return_value=local), \
                patch("ap_control_tower.extraction.document_ai.process_invoice_bytes", return_value=managed):
            result = extract_uploaded_document("dynata.pdf", b"pdf")
        self.assertEqual(result.document["project_reference"], "ORD-1796742-G6C4-ES01")
        self.assertEqual(result.field_confidences["project_reference"], Decimal("0.95"))


if __name__ == "__main__":
    unittest.main()
