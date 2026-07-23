"""Eval de historial Trial: SQLite hermético, mismo modelo usado en Postgres."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


@dataclass
class FakeResult:
    doc_id: str
    archivo: str
    document: dict
    engine: str = "google_document_ai_invoice_parser"
    confidence: Decimal = Decimal("0.88")
    pages: int = 1
    text_chars: int = 250
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)


def main() -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from ap_control_tower.extraction.schema import empty_document
    from ap_control_tower.persistence.models_sql import Base, TrialDocument, TrialRun
    from ap_control_tower.persistence.trial_repository import (
        delete_trial_run, list_trial_runs, load_trial_run, save_trial_session)
    from ap_control_tower.ui.trial import session as trial_session

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    state = trial_session.new_session()
    document = empty_document()
    document.update({
        "document_type": "invoice",
        "proveedor_nombre_comercial": "Proveedor Test SL",
        "proveedor_tax_id": "ESB12345678",
        "cliente_tax_id": "ESB87654321",
        "numero_factura": "F-001",
        "importe_total": "121.00",
        "moneda": "EUR",
        "iban": "ES9121000418450200051332",
        "proveedor_cuenta_bancaria": "12345678901234567890",
    })
    long_doc_id = "02. REMOTE 2026-05-Invoice-050IN26048878-Brand up slu.pdf"
    result = FakeResult(
        long_doc_id, long_doc_id, document,
        field_confidences={"numero_factura": Decimal("0.91")})
    trial_session.add_document(
        state, result, 1.25, file_hash="b" * 64, source="correo-ap")
    trial_session.record_intake(state, "correo-ap", 1)
    state.review_decisions[long_doc_id] = {
        "status": "confirmed", "actor": "Ana", "timestamp": "2026-07-12T12:00:00Z"}
    state.approval_decisions[long_doc_id] = {
        "status": "approved", "actor": "Bruno", "timestamp": "2026-07-12T12:05:00Z"}
    state.supplier_master_summary = {
        "source": "sage-xlsx", "fingerprint": "abc123", "active_vendors": 25}
    state.supplier_resolutions[long_doc_id] = {
        "status": "matched", "method": "fuzzy_name", "candidate_count": 1,
        "score": 0.91, "tax_id_confirmed": False,
        "warning": "proveedor vinculado por similitud de nombre, sin tax ID que lo confirme"}

    print("== Guardar y recuperar corrida ==")
    with Session(engine) as db:
        save_trial_session(db, state)
        db.commit()
        runs = list_trial_runs(db)
        loaded = load_trial_run(db, state.audit.run_id)
        check(len(runs) == 1 and runs[0]["documents"] == 1,
              "lista una corrida persistida")
        check(loaded is not None and len(loaded.results) == 1,
              "recupera resultado estructurado")
        check(loaded is not None and loaded.audit.verify_chain(),
              "recupera audit trail con cadena íntegra")
        check(loaded is not None and loaded.proc_seconds[long_doc_id] == 1.25,
              "recupera tiempo de procesamiento")
        check(loaded is not None and loaded.audit.events[1].invoice_id == long_doc_id,
              "auditoría conserva identificador real mayor a 48 caracteres")
        check(loaded is not None
              and loaded.review_decisions[long_doc_id]["status"] == "confirmed"
              and loaded.approval_decisions[long_doc_id]["status"] == "approved",
              "recupera decisiones de revisión y propuesta de pago")
        check(loaded is not None and loaded.file_hashes[long_doc_id] == "b" * 64
              and loaded.sources[long_doc_id] == "correo-ap",
              "recupera metadatos para continuar la corrida")
        check(loaded is not None
              and loaded.supplier_master_summary["active_vendors"] == 25
              and loaded.supplier_resolutions[long_doc_id]["method"] == "fuzzy_name",
              "recupera resumen y resolución Sage sin persistir el maestro")

        stored = db.query(TrialDocument).one()
        blob = str(stored.document)
        check("ES9121000418450200051332" not in blob and "ESB12345678" not in blob,
              "IBAN y tax IDs completos no se persisten")
        check(stored.file_hash == "b" * 64 and not hasattr(stored, "pdf_bytes"),
              "persiste hash y no existe columna de bytes PDF")

        print("== Idempotencia y borrado ==")
        save_trial_session(db, state)
        db.commit()
        check(db.query(TrialRun).count() == 1 and db.query(TrialDocument).count() == 1,
              "guardar dos veces no duplica")
        state.results.append(result)  # simula sesión dañada por importación doble
        save_trial_session(db, state)
        db.commit()
        check(db.query(TrialDocument).count() == 1,
              "persistencia defensiva colapsa doc_id repetidos")
        check(delete_trial_run(db, state.audit.run_id), "borrado explícito devuelve True")
        db.commit()
        check(db.query(TrialRun).count() == 0 and db.query(TrialDocument).count() == 0,
              "borrado elimina corrida y documentos")
        check(load_trial_run(db, state.audit.run_id) is None,
              "corrida borrada ya no se consulta")

    if failures:
        print(f"\nTRIAL PERSISTENCE ROJO: {len(failures)} falla(s)")
        return 1
    print("\nTRIAL PERSISTENCE VERDE: historial, masking, audit y borrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
