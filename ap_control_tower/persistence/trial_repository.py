"""Persistencia mínima del Trial: resultados, métricas y auditoría, sin PDF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import AuditEvent, AuditTrail
from ..extraction.pdf_poc import PocResult
from ..extraction.schema import FIELD_ORDER
from .masking import mask_account, mask_iban, mask_tax_id
from .models_sql import AuditoriaEvento, TrialDocument, TrialRun
from .repositories import persist_audit_trail, verify_persisted_chain


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _masked_document(document: dict) -> dict:
    """Minimiza lo persistido y enmascara identificadores sensibles."""
    clean = {field: _json_value(document.get(field)) for field in FIELD_ORDER}
    clean["iban"] = mask_iban(clean.get("iban"))
    if clean.get("iban"):
        clean["iban_enmascarado"] = True
    clean["proveedor_cuenta_bancaria"] = mask_account(
        clean.get("proveedor_cuenta_bancaria"))
    clean["proveedor_tax_id"] = mask_tax_id(clean.get("proveedor_tax_id"))
    clean["cliente_tax_id"] = mask_tax_id(clean.get("cliente_tax_id"))
    return clean


def _metrics(trial_session) -> dict:
    results = trial_session.results
    informed = [
        float(confidence)
        for result in results
        for confidence in result.field_confidences.values()
    ]
    return {
        "documents": len(results) + len(trial_session.errors),
        "successful": len(results),
        "errors": len(trial_session.errors),
        "invoices": sum(
            1 for result in results
            if result.document.get("document_type") == "invoice"),
        "with_warnings": sum(1 for result in results if result.warnings),
        "confidence": (sum(informed) / len(informed)) if informed else None,
        "processing_seconds": round(float(trial_session.processing_seconds), 3),
    }


def save_trial_session(db: Session, trial_session) -> TrialRun:
    """Upsert idempotente de una sesión. Reemplaza documentos de ese run_id."""
    run_id = trial_session.audit.run_id
    row = db.get(TrialRun, run_id)
    if row is None:
        row = TrialRun(run_id=run_id, created_at=datetime.fromisoformat(
            trial_session.created_at.replace("Z", "+00:00")))
        db.add(row)
    row.updated_at = datetime.now().astimezone()
    row.source = "trial"
    row.document_count = len(trial_session.results)
    row.error_count = len(trial_session.errors)
    row.processing_seconds = Decimal(str(round(trial_session.processing_seconds, 3)))
    row.metrics = _metrics(trial_session)
    row.errors = [
        {"filename": filename, "detail": (detail or "")[:240]}
        for filename, detail in trial_session.errors
    ]
    db.flush()

    db.execute(delete(TrialDocument).where(TrialDocument.run_id == run_id))
    for result in trial_session.results:
        db.add(TrialDocument(
            run_id=run_id,
            doc_id=result.doc_id,
            filename=getattr(result, "archivo", result.doc_id),
            file_hash=trial_session.file_hashes.get(result.doc_id),
            source=trial_session.sources.get(result.doc_id, "carga-manual"),
            engine=result.engine,
            pages=result.pages,
            text_chars=result.text_chars,
            confidence=Decimal(str(result.confidence)),
            warnings=_json_value(result.warnings),
            document=_masked_document(result.document),
            field_confidences=_json_value(result.field_confidences),
            processing_seconds=Decimal(str(
                trial_session.proc_seconds.get(result.doc_id, 0.0))),
        ))
    persist_audit_trail(db, trial_session.audit)
    db.flush()
    return row


@dataclass(frozen=True)
class StoredTrialRun:
    run_id: str
    created_at: datetime
    updated_at: datetime
    metrics: dict
    results: list[PocResult]
    errors: list[tuple[str, str]]
    proc_seconds: dict[str, float]
    processing_seconds: float
    audit: AuditTrail


def list_trial_runs(db: Session, limit: int = 25) -> list[dict]:
    rows = db.scalars(
        select(TrialRun).order_by(TrialRun.created_at.desc()).limit(limit)).all()
    return [{
        "run_id": row.run_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "documents": row.document_count,
        "errors": row.error_count,
        "processing_seconds": float(row.processing_seconds or 0),
        "metrics": row.metrics or {},
    } for row in rows]


def _load_audit(db: Session, run_id: str) -> AuditTrail:
    rows = db.scalars(
        select(AuditoriaEvento).where(AuditoriaEvento.run_id == run_id)
        .order_by(AuditoriaEvento.seq)).all()
    audit = AuditTrail(run_id=run_id, commit=(rows[0].commit if rows else "trial-session") or "")
    audit.events = [AuditEvent(
        seq=row.seq, ts=row.ts, run_id=row.run_id, commit=row.commit or "",
        agent=row.actor, action=row.accion, invoice_id=row.invoice_id,
        control_id=row.control_id, result=row.resultado,
        evidence=row.evidencia or {}, prev_hash=row.prev_hash, hash=row.hash,
    ) for row in rows]
    return audit


def load_trial_run(db: Session, run_id: str) -> StoredTrialRun | None:
    row = db.get(TrialRun, run_id)
    if row is None:
        return None
    documents = db.scalars(
        select(TrialDocument).where(TrialDocument.run_id == run_id)
        .order_by(TrialDocument.id)).all()
    results = [PocResult(
        doc_id=item.doc_id,
        archivo=item.filename,
        pages=item.pages,
        text_chars=item.text_chars,
        confidence=Decimal(str(item.confidence)),
        warnings=list(item.warnings or []),
        document=dict(item.document or {}),
        engine=item.engine,
        field_confidences={
            key: Decimal(str(value))
            for key, value in (item.field_confidences or {}).items()
        },
    ) for item in documents]
    proc_seconds = {
        item.doc_id: float(item.processing_seconds or 0) for item in documents}
    audit = _load_audit(db, run_id)
    if audit.events and not verify_persisted_chain(db, run_id):
        raise ValueError(f"cadena de auditoría inconsistente para {run_id}")
    return StoredTrialRun(
        run_id=row.run_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metrics=row.metrics or {},
        results=results,
        errors=[(item.get("filename", ""), item.get("detail", ""))
                for item in (row.errors or [])],
        proc_seconds=proc_seconds,
        processing_seconds=float(row.processing_seconds or 0),
        audit=audit,
    )


def delete_trial_run(db: Session, run_id: str) -> bool:
    row = db.get(TrialRun, run_id)
    if row is None:
        return False
    db.execute(delete(AuditoriaEvento).where(AuditoriaEvento.run_id == run_id))
    db.delete(row)
    db.flush()
    return True
