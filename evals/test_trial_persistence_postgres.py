"""Smoke test del historial Trial contra PostgreSQL real configurado por entorno."""

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
    archivo: str
    document: dict
    engine: str = "google_document_ai_invoice_parser"
    confidence: Decimal = Decimal("0.90")
    pages: int = 1
    text_chars: int = 100
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=lambda: {
        "numero_factura": Decimal("0.90")})


def main() -> int:
    from ap_control_tower.extraction.schema import empty_document
    from ap_control_tower.persistence.session import build_engine, session_scope
    from ap_control_tower.persistence.trial_repository import (
        delete_trial_run, load_trial_run, save_trial_session)
    from ap_control_tower.ui.trial import session as trial_session

    state = trial_session.new_session()
    state.audit.run_id = "trial-postgres-smoke"
    for event in state.audit.events:
        event.run_id = state.audit.run_id
        event.hash = event.compute_hash()
    document = empty_document()
    document.update({
        "document_type": "invoice", "numero_factura": "PG-001",
        "proveedor_nombre_comercial": "Proveedor PG",
        "importe_total": "10.00", "moneda": "EUR",
    })
    result = FakeResult("postgres-smoke.pdf", "postgres-smoke.pdf", document)
    trial_session.add_document(
        state, result, 0.2, file_hash="c" * 64, source="smoke-test")
    trial_session.record_intake(state, "smoke-test", 1)

    engine = build_engine()
    with session_scope(engine) as db:
        delete_trial_run(db, state.audit.run_id)
    with session_scope(engine) as db:
        save_trial_session(db, state)
    with session_scope(engine) as db:
        loaded = load_trial_run(db, state.audit.run_id)
        assert loaded is not None
        assert len(loaded.results) == 1
        assert loaded.audit.verify_chain()
        assert loaded.results[0].document["numero_factura"] == "PG-001"
    with session_scope(engine) as db:
        assert delete_trial_run(db, state.audit.run_id)
    with session_scope(engine) as db:
        assert load_trial_run(db, state.audit.run_id) is None
    engine.dispose()
    print("TRIAL POSTGRES VERDE: migración, save/load, audit y delete reales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
