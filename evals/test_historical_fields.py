from __future__ import annotations

import unittest

from ap_control_tower.extraction.historical_fields import extract_historical_fields


class HistoricalFieldExtractionTests(unittest.TestCase):
    def test_structured_company_registries_are_canonical(self):
        cases = {
            "KVK: 76348946": "KVK 76348946",
            "SIRET 49540260400054": "SIRET 49540260400054",
            "SIREN: 832879621": "SIREN 832879621",
            "Handelsregister HRB 56041": "HRB 56041",
            "Company Registration No. 4467531": "Company Registration No. 4467531",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                evidence = extract_historical_fields(text).proveedor_registro
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence.value, expected)

    def test_legal_prose_does_not_become_a_registry_identifier(self):
        for text in (
            "Registro Mercantil de Madrid. Tomo 9, folio 22.",
            "Reglamento General de Protección de Datos",
            "Taxes and regulatory fees",
            "Gracias por registrarte tu móvil.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_historical_fields(text).proveedor_registro)

    def test_service_period_requires_an_explicit_anchor(self):
        fields = extract_historical_fields("Periodo de servicio: 01/06/2026 - 30/06/2026")
        self.assertEqual(fields.periodo_servicio_desde.value, "2026-06-01")
        self.assertEqual(fields.periodo_servicio_hasta.value, "2026-06-30")

        month = extract_historical_fields("Cuota JULIO 2026")
        self.assertEqual(month.periodo_servicio_desde.value, "2026-07-01")
        self.assertEqual(month.periodo_servicio_hasta.value, "2026-07-31")

        unlabelled = extract_historical_fields("Invoice date March 2026")
        self.assertIsNone(unlabelled.periodo_servicio_desde)
        self.assertIsNone(unlabelled.periodo_servicio_hasta)

    def test_single_service_date_is_preserved(self):
        fields = extract_historical_fields("Date of service: 17 June 2026")
        self.assertEqual(fields.periodo_servicio_desde.value, "2026-06-17")
        self.assertEqual(fields.periodo_servicio_hasta.value, "2026-06-17")

    def test_split_date_table_maps_the_service_column_not_invoice_date(self):
        fields = extract_historical_fields(
            "Invoice date: Date of service: Payment due date:\n"
            "02/03/2026 31/03/2026 16/03/2026"
        )
        self.assertEqual(fields.periodo_servicio_desde.value, "2026-03-31")
        self.assertEqual(fields.periodo_servicio_hasta.value, "2026-03-31")

    def test_bilingual_ranges_and_split_tables_are_supported(self):
        spanish = extract_historical_fields("Periodo desde: 30/01/2026 Hasta: 27/02/2026")
        self.assertEqual(spanish.periodo_servicio_desde.value, "2026-01-30")
        self.assertEqual(spanish.periodo_servicio_hasta.value, "2026-02-27")

        english = extract_historical_fields(
            "SERVICES PROVISION - Study A - From 02/03/26 to 02/12/26"
        )
        self.assertEqual(english.periodo_servicio_desde.value, "2026-02-03")
        self.assertEqual(english.periodo_servicio_hasta.value, "2026-02-12")

        table = extract_historical_fields(
            "Billing Period\nWorkplace Pro Monthly Apr 30, 2026 May 29,\nQuantity: 2 2026"
        )
        self.assertEqual(table.periodo_servicio_desde.value, "2026-04-30")
        self.assertEqual(table.periodo_servicio_hasta.value, "2026-05-29")

    def test_line_item_cuotas_do_not_define_one_document_period(self):
        fields = extract_historical_fields(
            "Cuotas de Comunicaciones (1 Nov. a 30 Nov.)\n"
            "Cuotas de Licencias y Equipamientos (1 Dic. a 31 Dic.)"
        )
        self.assertIsNone(fields.periodo_servicio_desde)
        self.assertIsNone(fields.periodo_servicio_hasta)

    def test_explicit_payment_terms_are_short_and_auditable(self):
        cases = {
            "Payment Terms: Net 30": "Net 30",
            "This invoice is payable within 30 days.": "payable within 30 days",
            "Forma de pago: Recibo Domiciliado": "Recibo Domiciliado",
            "Condiciones de pago: R.SEPA a 3 días, días fijos de pago 15 y 25":
                "R.SEPA a 3 días, días fijos de pago 15 y 25",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                evidence = extract_historical_fields(text).condiciones_pago
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence.value, expected)

    def test_unrelated_long_sentence_is_not_payment_terms(self):
        text = "Si no lo ha recibido, puede solicitarlo a Atención al Cliente conforme a las condiciones generales."
        self.assertIsNone(extract_historical_fields(text).condiciones_pago)

    def test_bare_days_are_not_payment_terms_without_payment_context(self):
        self.assertIsNone(extract_historical_fields("Periodo facturado: 28 días").condiciones_pago)
        fields = extract_historical_fields("Vencimiento: En la forma establecida")
        self.assertEqual(fields.condiciones_pago.value, "En la forma establecida")


if __name__ == "__main__":
    unittest.main()
