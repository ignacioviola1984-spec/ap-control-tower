"""Fase 1.5: maestro Sage, identidad de proveedor y auditoria."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
import os
import sys
from pathlib import Path
import unittest
import unittest.mock

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower.matching import (
    FUZZY_SIMILARITY_THRESHOLD,
    meets_fuzzy_threshold,
)
from ap_control_tower.sage import (
    FUZZY_VENDOR_FYI,
    SageMasterError,
    load_vendor_master_xlsx,
    normalize_supplier_name,
    resolve_document_supplier,
)
from ap_control_tower.audit import AuditTrail
from ap_control_tower.ui.trial import session as trial_session
from ap_control_tower.ui.trial import workflow


HEADERS = [
    "Cód. cliente",
    "Cód. proveedor",
    "Cód. contable",
    "Cód. categoría cliente",
    "Sigla nación",
    "CIF/DNI",
    "CIF europeo",
    "Razón social",
    "Nombre cli/pro.",
    "I.B.A.N.",
    "Cód. banco",
    "Cód. condiciones",
    "Baja empresa",
    "Fecha baja",
]


def workbook_bytes(rows: list[list]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Proveedores"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def vendor_row(code: str, name: str, tax_id: str = "", *, country: str = "ES",
               iban: str = "168", inactive: bool = False) -> list:
    return [
        "168", code, f"4100{code}", "PRO", country, tax_id,
        f"{country}{tax_id}" if tax_id else "168", name, name,
        iban, "168", "30", "Sí" if inactive else "No", "168",
    ]


def valid_master_bytes() -> bytes:
    return workbook_bytes([
        vendor_row("V001", "Empresa S.L.U.", "B12345678"),
        vendor_row("V002", "Compañía Médica S.A.", "A87654321"),
        vendor_row("V003", "Servicios Creativos Iberia SL", ""),
        vendor_row("V004", "Acme Servicios Logísticos SL", ""),
        vendor_row("V005", "Acme Servicios Logísticos Europa SL", ""),
    ])


def document(*, name: str, tax_id: str = "") -> dict:
    return {
        "document_type": "invoice",
        "proveedor_razon_social_legal": name,
        "proveedor_nombre_comercial": name,
        "proveedor_tax_id": tax_id,
        "numero_factura": "F-001",
        "fecha_emision": "2026-07-01",
        "moneda": "EUR",
        "importe_total": "100.00",
    }


@dataclass
class FakeResult:
    doc_id: str
    document: dict
    engine: str = "test"
    confidence: Decimal = Decimal("0.90")
    pages: int = 1
    text_chars: int = 100
    warnings: list[str] = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)


class SageVendorMasterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.master = load_vendor_master_xlsx(
            valid_master_bytes(), filename="proveedores.xlsx")

    def test_shared_fuzzy_threshold_is_canonical(self) -> None:
        self.assertEqual(FUZZY_SIMILARITY_THRESHOLD, 0.85)
        self.assertTrue(meets_fuzzy_threshold("EF-2026-045", "EF-2026-046"))
        self.assertFalse(meets_fuzzy_threshold("INV-1", "INV-2"))

    def test_sage_168_placeholder_is_empty_only_in_optional_fields(self) -> None:
        first = self.master.vendors[0]
        self.assertEqual(first.source_id, "V001")
        self.assertTrue(first.active)
        self.assertIsNone(first.iban)
        self.assertIsNone(first.bank_code)

    def test_strong_name_normalization_legal_suffix_accents_and_case(self) -> None:
        self.assertEqual(
            normalize_supplier_name("Empresa SL"),
            normalize_supplier_name("EMPRESA S.L.U."),
        )
        self.assertEqual(
            normalize_supplier_name("COMPAÑÍA MÉDICA S.A."),
            normalize_supplier_name("compania medica"),
        )
        suffix = resolve_document_supplier(document(name="Empresa SL"), self.master)
        accents = resolve_document_supplier(document(name="COMPANIA MEDICA"), self.master)
        self.assertEqual((suffix.status, suffix.method), ("matched", "exact_name"))
        self.assertEqual((accents.status, accents.method), ("matched", "exact_name"))

    def test_tax_id_is_authoritative_and_name_is_not_used_on_tax_miss(self) -> None:
        matched = resolve_document_supplier(
            document(name="Nombre completamente distinto", tax_id="ESB12345678"),
            self.master,
        )
        self.assertTrue(matched.accepted)
        self.assertEqual(matched.method, "tax_id")
        self.assertTrue(matched.tax_id_confirmed)

        missed = resolve_document_supplier(
            document(name="Empresa SL", tax_id="ES00000000"), self.master
        )
        self.assertEqual((missed.status, missed.method), ("not_found", "tax_id"))
        self.assertFalse(missed.accepted)

    def test_unique_fuzzy_match_is_accepted_with_exact_fyi(self) -> None:
        resolution = resolve_document_supplier(
            document(name="Servicio Creativo Iberia"), self.master
        )
        self.assertTrue(resolution.accepted)
        self.assertEqual(resolution.method, "fuzzy_name")
        self.assertEqual(resolution.warning, FUZZY_VENDOR_FYI)
        self.assertGreaterEqual(resolution.score or 0, FUZZY_SIMILARITY_THRESHOLD)

    def test_two_legitimate_similar_vendors_are_ambiguous_not_merged(self) -> None:
        resolution = resolve_document_supplier(
            document(name="Acme Servicios Logisticos Europ"), self.master
        )
        self.assertEqual(resolution.status, "ambiguous")
        self.assertEqual(resolution.method, "fuzzy_name")
        self.assertEqual(resolution.candidate_count, 2)
        self.assertFalse(resolution.accepted)

    def test_no_candidate_is_not_found(self) -> None:
        resolution = resolve_document_supplier(
            document(name="Proveedor que no existe"), self.master
        )
        self.assertEqual(resolution.status, "not_found")
        self.assertEqual(resolution.candidate_count, 0)

    def test_customer_export_is_rejected_instead_of_silently_imported(self) -> None:
        rows = [
            [str(index), "168", f"4300{index}", "CLI", "ES", f"B{index:08d}",
             f"ESB{index:08d}", f"Cliente {index}", f"Cliente {index}",
             "168", "168", "0", "No", ""]
            for index in range(1, 5)
        ]
        with self.assertRaisesRegex(SageMasterError, "maestro de clientes"):
            load_vendor_master_xlsx(workbook_bytes(rows), filename="clientes.xlsx")

    def test_invalid_xlsx_returns_safe_error(self) -> None:
        with self.assertRaisesRegex(SageMasterError, "XLSX válido"):
            load_vendor_master_xlsx(b"not-an-xlsx", filename="roto.xlsx")

    def test_session_reconciles_visible_fyi_and_hash_chained_audit(self) -> None:
        active = trial_session.new_session()
        fuzzy = FakeResult("DOC-FUZZY", document(name="Servicio Creativo Iberia"))
        trial_session.add_document(active, fuzzy)
        summary = trial_session.load_sage_vendor_master(
            active, "proveedores.xlsx", valid_master_bytes()
        )
        self.assertEqual(summary["active_vendors"], 5)
        self.assertIn(FUZZY_VENDOR_FYI, fuzzy.warnings)
        self.assertNotIn(FUZZY_VENDOR_FYI, workflow.review_reasons(fuzzy))
        self.assertEqual(
            active.supplier_resolutions["DOC-FUZZY"]["method"], "fuzzy_name"
        )
        self.assertTrue(any(
            event.action == "proveedor-vinculado-por-similitud-nombre"
            and event.invoice_id == "DOC-FUZZY"
            and event.result == "fyi"
            for event in active.audit.events
        ))
        self.assertTrue(active.audit.verify_chain())

        audit_blob = " ".join(str(event.evidence) for event in active.audit.events)
        self.assertNotIn("Servicios Creativos Iberia", audit_blob)
        self.assertNotIn("B12345678", audit_blob)

    def test_ambiguous_and_not_found_are_routed_to_human_review(self) -> None:
        active = trial_session.new_session()
        trial_session.load_sage_vendor_master(
            active, "proveedores.xlsx", valid_master_bytes()
        )
        ambiguous = FakeResult(
            "DOC-AMB", document(name="Acme Servicios Logisticos Europ")
        )
        missing = FakeResult("DOC-NONE", document(name="Proveedor inexistente"))
        trial_session.add_document(active, ambiguous)
        trial_session.add_document(active, missing)
        self.assertTrue(workflow.requires_human_review(ambiguous))
        self.assertTrue(workflow.requires_human_review(missing))
        self.assertTrue(any(
            event.action == "proveedor-ambiguo-sage" for event in active.audit.events
        ))
        self.assertTrue(any(
            event.action == "proveedor-no-encontrado-sage" for event in active.audit.events
        ))
        self.assertTrue(active.audit.verify_chain())

    def test_new_unresolved_match_reverts_stale_confirmations_and_approval(self) -> None:
        active = trial_session.new_session()
        unresolved = FakeResult(
            "DOC-STALE", document(name="Proveedor inexistente")
        )
        trial_session.add_document(active, unresolved)
        active.review_decisions["DOC-STALE"] = {
            "status": "confirmed", "actor": "Revisora"
        }
        active.approval_decisions["DOC-STALE"] = {
            "status": "approved", "actor": "Aprobador"
        }

        trial_session.load_sage_vendor_master(
            active, "proveedores.xlsx", valid_master_bytes()
        )

        self.assertNotIn("DOC-STALE", active.review_decisions)
        self.assertNotIn("DOC-STALE", active.approval_decisions)
        safe = active.supplier_resolutions["DOC-STALE"]
        self.assertTrue(safe["review_confirmation_reverted"])
        self.assertTrue(safe["payment_decision_reverted"])
        event = next(
            item for item in reversed(active.audit.events)
            if item.invoice_id == "DOC-STALE"
        )
        self.assertTrue(event.evidence["confirmacion_previa_revertida"])
        self.assertTrue(event.evidence["decision_pago_previa_revertida"])
        self.assertTrue(active.audit.verify_chain())


class VendorMasterPaymentControlsTests(unittest.TestCase):
    """El maestro debe DISPARAR controles de pago, no solo mostrarse."""

    IBAN_REGISTRADO = "ES9121000418450200051332"
    IBAN_DISTINTO = "ES6800495144082310038771"

    def _master(self, **kwargs):
        # Se acompaña de un proveedor activo: un maestro donde TODOS estén de
        # baja se rechaza al cargar, y ese no es el caso bajo prueba.
        return load_vendor_master_xlsx(
            workbook_bytes([
                vendor_row("V001", "Empresa S.L.U.", "B12345678", **kwargs),
                vendor_row("V999", "Otro Proveedor Activo SL", "B11111111"),
            ]),
            filename="proveedores.xlsx",
        )

    def _document(self, iban: str | None):
        doc = document(name="Empresa S.L.U.", tax_id="B12345678")
        doc["iban"] = iban
        return doc

    def _reasons(self, resolution) -> list[str]:
        result = FakeResult(doc_id="DOC-1", document=self._document(None))
        result.supplier_resolution = resolution.safe_dict()
        return workflow.review_reasons(result)

    def test_invoice_bank_account_differing_from_sage_is_flagged(self) -> None:
        """El control de mayor impacto: desvío del pago a otra cuenta."""
        master = self._master(iban=self.IBAN_REGISTRADO)

        resolution = resolve_document_supplier(
            self._document(self.IBAN_DISTINTO), master)

        self.assertIs(resolution.iban_matches, False)
        motivos = " ".join(self._reasons(resolution))
        self.assertIn("cuenta de cobro", motivos)
        # El motivo nunca puede volcar ninguno de los dos IBAN.
        self.assertNotIn(self.IBAN_REGISTRADO, motivos)
        self.assertNotIn(self.IBAN_DISTINTO, motivos)

    def test_matching_bank_account_raises_no_reason(self) -> None:
        master = self._master(iban=self.IBAN_REGISTRADO)

        resolution = resolve_document_supplier(
            self._document(self.IBAN_REGISTRADO), master)

        self.assertIs(resolution.iban_matches, True)
        self.assertEqual(self._reasons(resolution), [])

    def test_masked_invoice_iban_is_not_comparable_and_raises_no_alarm(self) -> None:
        """Un IBAN enmascarado oculta dígitos: no se puede afirmar que difiere."""
        master = self._master(iban=self.IBAN_REGISTRADO)

        resolution = resolve_document_supplier(
            self._document("ES91 **** **** **** 1332"), master)

        self.assertIsNone(resolution.iban_matches)
        self.assertEqual(self._reasons(resolution), [])

    def test_sage_master_without_iban_raises_no_alarm(self) -> None:
        master = self._master()  # columna I.B.A.N. con el placeholder "168"

        resolution = resolve_document_supplier(
            self._document(self.IBAN_DISTINTO), master)

        self.assertIsNone(resolution.iban_matches)
        self.assertEqual(self._reasons(resolution), [])

    def test_inactive_vendor_is_distinguished_from_an_unknown_one(self) -> None:
        """Dar de baja y no existir exigen acciones distintas del revisor."""
        master = self._master(inactive=True)

        de_baja = resolve_document_supplier(
            self._document(None), master)
        self.assertEqual(de_baja.status, "inactive")
        self.assertFalse(de_baja.accepted)
        self.assertIn("dado de baja", " ".join(self._reasons(de_baja)))

        desconocido = resolve_document_supplier(
            document(name="Otra Cosa SL", tax_id="B99999999"), master)
        self.assertEqual(desconocido.status, "not_found")
        self.assertIn("no dado de alta", " ".join(self._reasons(desconocido)))

    def test_without_a_loaded_master_no_vendor_reason_is_emitted(self) -> None:
        """Sin maestro no se bloquea nada: retendría el lote completo."""
        result = FakeResult(doc_id="DOC-SIN-MAESTRO",
                            document=self._document(self.IBAN_DISTINTO))

        self.assertEqual(workflow.review_reasons(result), [])

    def test_resolution_never_publishes_the_registered_iban(self) -> None:
        master = self._master(iban=self.IBAN_REGISTRADO)

        resolution = resolve_document_supplier(
            self._document(self.IBAN_DISTINTO), master)

        serialized = str(resolution.safe_dict())
        self.assertNotIn(self.IBAN_REGISTRADO, serialized)
        self.assertIn("iban_matches", resolution.safe_dict())


class ProvisionedMasterTests(unittest.TestCase):
    """El maestro es dato de la instalación: se aplica solo, sin que el
    operador tenga que acordarse de subirlo en cada sesión."""

    def test_session_applies_the_installed_master_without_user_action(self) -> None:
        import tempfile

        from ap_control_tower.sage import provisioning

        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "vendor_master.xlsx"
            ruta.write_bytes(valid_master_bytes())
            with unittest.mock.patch.dict(
                os.environ, {provisioning.ENV_VAR: str(ruta)}, clear=False
            ):
                session = trial_session.new_session()

        self.assertIsNotNone(session.supplier_master)
        self.assertEqual(session.supplier_master_summary["active_vendors"], 5)
        self.assertTrue(any(
            event.action == "maestro-proveedores-sage-aplicado"
            for event in session.audit.events
        ))

    def test_missing_master_does_not_break_the_session(self) -> None:
        from ap_control_tower.sage import provisioning

        with unittest.mock.patch.dict(
            os.environ, {provisioning.ENV_VAR: "/no/existe/maestro.xlsx"}, clear=False
        ):
            session = trial_session.new_session()

        self.assertIsNone(session.supplier_master)

    def test_new_vendor_joins_the_master_and_queues_for_sage(self) -> None:
        """Alta → concilia en el acto y viaja con el lote de pago."""
        session = trial_session.TrialSession(
            audit=AuditTrail(run_id="alta", commit="test"))
        session.supplier_master = load_vendor_master_xlsx(
            valid_master_bytes(), filename="proveedores.xlsx")
        antes = len(session.supplier_master.active_vendors)

        fila = {
            "Código cuenta": "41000999", "Descripción": "NUEVO PROVEEDOR SL",
            "Clien./Prov.": "Proveedor", "Sigla": "ES", "CIF/DNI": "B44444444",
            "Cód. divisa": "EUR", "Ind. prorrata": "", "Bloqueada": "No",
        }
        trial_session.register_new_vendor(
            session, fila, {"iban": "ES9121000418450200051332", "bic": ""})

        self.assertEqual(len(session.supplier_master.active_vendors), antes + 1)
        self.assertEqual(len(session.pending_vendors), 1)
        # Una factura de ese proveedor concilia sin esperar al export del ERP.
        resolution = resolve_document_supplier(
            document(name="NUEVO PROVEEDOR SL", tax_id="B44444444"),
            session.supplier_master,
        )
        self.assertTrue(resolution.accepted)


if __name__ == "__main__":
    unittest.main()
