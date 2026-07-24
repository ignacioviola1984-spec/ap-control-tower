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
    refine_mapped_document,
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
CIF B12345678
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
                entity("supplier_tax_id", "B12345678"),
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
        self.assertEqual(doc["metodo_pago"], "no_indicado")
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
SERVICIOS VERDES UNA TINTA S.L.U. N.I.F: B12345678
"""
        document = SimpleNamespace(
            text=text,
            pages=[object()],
            entities=[
                entity("invoice_id", "FV-1"),
                entity("invoice_date", "05/05/2026", "2026-05-05"),
                entity("supplier_name", "grupo"),
                entity("supplier_tax_id", "B12345678"),
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

    def test_brand_up_context_repairs_inverted_supplier_and_tax_ids(self):
        text = """Orange Espagne, S.A.U. CIF A82009812
FACTURA A-1
CLIENTE: BRAND UP S.L.U. CIF B85902583
TOTAL EUR 121,00
"""
        document = SimpleNamespace(
            text=text,
            pages=[object()],
            entities=[
                entity("invoice_id", "A-1"),
                entity("supplier_name", "BRAND UP S.L.U."),
                entity("supplier_tax_id", "B85902583"),
                entity("receiver_name", "Orange Espagne, S.A.U."),
                entity("receiver_tax_id", "A82009812"),
                entity("currency", "EUR", "EUR"),
                entity("total_amount", "121,00", "121.00"),
            ],
        )

        result = map_document_ai_result("orange.pdf", document)

        self.assertIn("Orange", result.document["proveedor_nombre_comercial"])
        self.assertEqual(result.document["proveedor_tax_id"], "A82009812")
        self.assertIn("BRAND UP", result.document["cliente_nombre"])
        self.assertEqual(result.document["cliente_tax_id"], "B85902583")

    def test_refinement_repairs_strong_total_and_thousands_scale(self):
        document = empty_document()
        document.update({"document_type": "invoice", "importe_total": "2.86"})

        refine_mapped_document(document, "GROSS : £ 2,860 GBP", empty_document())

        self.assertEqual(document["importe_total"], "2860.00")

    def test_amount_due_is_a_balance_and_never_overwrites_the_invoice_total(self):
        """Ecotisa: "Importe adeudado 0,00" pisaba el total y lo dejaba en cero."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "36.68",
            "importe_iva": "7.70",
            "importe_total": "44.38",
        })

        refine_mapped_document(
            document,
            "Base imponible 36,68 €\nImpuesto 7,70 €\nTotal 44,38 €\n"
            "Pagado en 03/02/2026\nImporte adeudado 0,00 €\n",
            empty_document(),
        )

        self.assertEqual(document["importe_total"], "44.38")
        self.assertEqual(document["saldo_pendiente"], "0.00")

    def test_provision_of_funds_leaves_the_payable_amount_as_balance(self):
        """Elzaburu: el total es 6.467,44 pero sólo quedan 1.022,14 por pagar."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "6057.77",
            "importe_iva": "409.67",
            "importe_total": "6467.44",
        })

        refine_mapped_document(
            document,
            "Subtotal 6.057,77\n409,67\nTotal EUR 6.467,44\n"
            "Provision de Fondos recibida EUR 5.445,30\nImporte a pagar EUR 1.022,14\n",
            empty_document(),
        )

        self.assertEqual(document["importe_total"], "6467.44")
        self.assertEqual(document["saldo_pendiente"], "1022.14")

    def test_net_amount_is_solved_from_the_total_when_the_parser_guesses_wrong(self):
        """Iván Zimmermann: el neto venía con el valor del IVA (2.100 en vez de 10.000)."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "2100.00",
            "importe_iva": "2100.00",
            "importe_total": "10600.00",
        })

        refine_mapped_document(
            document,
            "Creativity services Bayer\n10.000,00 EU\nIVA (21%)\nIRPF (-15%)\n"
            "2.100,00 €\n-1.500,00 €\nTotal 10.600,00 EU\n",
            empty_document(),
        )

        self.assertEqual(document["importe_neto"], "10000.00")
        self.assertEqual(document["retencion_irpf"], "1500.00")

    def test_withholding_in_a_column_layout_is_derived_from_the_declared_rate(self):
        """Rebeca Ferrer: el importe del IRPF cae lejos de su etiqueta y no se leía."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "15.00",
            "importe_iva": "210.00",
            "importe_total": "1060.00",
        })

        refine_mapped_document(
            document,
            "Base imponible\n1.000,00\n% IVA\n21,00%\nIVA\n210,00\n"
            "% IRPF\n15,00%\nIRPF\n150,00\nTotal factura\n1.060,00\n",
            empty_document(),
        )

        self.assertEqual(document["importe_neto"], "1000.00")
        self.assertEqual(document["retencion_irpf"], "150.00")

    def test_vat_split_is_recovered_from_amounts_printed_in_the_document(self):
        """Endesa: el parser erró neto e IVA a la vez; la partición al 21% está impresa."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "259.39",
            "importe_iva": "68.19",
            "importe_total": "319.43",
        })

        refine_mapped_document(
            document,
            "Impuesto Electricidad 249,35 12,75\nIVA 21%\n263,99\n55,44\nTotal 319,43\n",
            empty_document(),
        )

        self.assertEqual(document["importe_neto"], "263.99")
        self.assertEqual(document["importe_iva"], "55.44")

    def test_loose_exemption_wording_does_not_zero_a_taxed_invoice(self):
        """El texto legal de una factura de suministro mencionaba "exento"."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "263.99",
            "importe_iva": "55.44",
            "importe_total": "319.43",
            "tipo_iva": "21",
            "proveedor_tax_id": "A81948077",
        })

        refine_mapped_document(
            document,
            "IVA 21% 55,44\nTotal 319,43\n"
            "El bono social esta exento de recargo segun la normativa vigente.\n",
            empty_document(),
        )

        self.assertEqual(document["tratamiento_iva"], "nacional")
        self.assertEqual(document["importe_iva"], "55.44")

    def test_free_text_and_sentinel_dates_never_reach_a_date_field(self):
        document = empty_document()
        document.update({"document_type": "invoice", "importe_total": "10.00"})

        refine_mapped_document(document, "Due date: 30 days from invoice issuance.", empty_document())

        self.assertIsNone(document["fecha_vencimiento_calculada"])

    def test_transfer_wins_over_the_accepted_cards_footer(self):
        """Qualzy pide transferencia y sólo lista las tarjetas que acepta."""
        document = empty_document()
        document.update({"document_type": "invoice", "importe_total": "1300.00"})

        refine_mapped_document(
            document,
            "Please transfer the total amount payable to the bank account listed above.\n"
            "Accepts Visa, Mastercard and American Express payments from customers worldwide.\n",
            empty_document(),
        )

        self.assertEqual(document["metodo_pago"], "transferencia")

    def test_labelled_tax_id_prefix_does_not_turn_a_spanish_freelancer_foreign(self):
        """Flor Murga: "DNI12817931P" hacía inferir el país "DN" -> extracomunitario."""
        document = empty_document()
        document.update({
            "document_type": "invoice",
            "importe_neto": "1250.00",
            "importe_iva": "262.50",
            "importe_total": "1325.00",
            "proveedor_tax_id": "DNI12817931P",
            "iban": "ES4001821294170203714052",
        })

        refine_mapped_document(
            document,
            "Florencia Murga\nDNI12817931P\nIVA 21,00%\nRetencion 15,00% 187,50 €\n"
            "TOTAL FACTURA 1.325,00 €\n",
            empty_document(),
        )

        self.assertEqual(document["proveedor_tax_id"], "12817931P")
        self.assertEqual(document["tratamiento_iva"], "nacional")

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
