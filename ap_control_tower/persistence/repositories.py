"""Repositorios: mapean el dominio del motor (puro) a las tablas y de vuelta.

Contrato de la Fase 1: el motor no cambia. La corrida se produce igual que hoy
y OPCIONALMENTE se persiste con ``persist_run``. Los maestros (proveedores,
OC, documentos) se hacen upsert por clave natural (idempotente); las tablas de
la corrida (controles, excepciones, lotes, auditoria) se reemplazan por run_id.
La auditoria es append-only y se revalida su cadena de hash antes de insertar.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import AuditTrail
from ..engine.controls import classify_document
from ..engine.lifecycle import phase_for_status
from ..models import (
    DOC_OTHER,
    DOC_PROFORMA,
    Dataset,
    Invoice,
    RunResult,
    Vendor,
)
from . import masking
from .models_sql import (
    Aprobacion,  # noqa: F401  (re-exportado para consumidores)
    AuditoriaEvento,
    ControlEjecutado,
    Documento,
    Excepcion,
    Factura,
    LoteFactura,
    LotePago,
    OrdenCompra,
    OrdenCompraLinea,
    Pago,  # noqa: F401
    Proveedor,
    RevisionHumana,
)


# ------------------------------------------------------------------ maestros
def upsert_vendor(session: Session, vendor: Vendor) -> Proveedor:
    row = session.scalar(
        select(Proveedor).where(Proveedor.codigo_interno == vendor.vendor_id))
    if row is None:
        row = Proveedor(codigo_interno=vendor.vendor_id)
        session.add(row)
    row.nombre_legal = vendor.name
    row.nombre_comercial = vendor.name
    row.tax_id = vendor.tax_id
    row.codigo_pais = vendor.country
    row.intercompany = vendor.intercompany
    row.categoria = vendor.category
    row.payment_terms_days = vendor.payment_terms_days
    row.razon_social_confirmada = vendor.razon_social_confirmada
    row.iban_autorizado = vendor.iban
    row.banco_autorizado = vendor.bank_name
    row.sepa_mandate_ref = vendor.sepa_mandate_ref
    row.estado = "retenido" if (not (vendor.tax_id or "").strip()
                                or not vendor.razon_social_confirmada) else "activo"
    session.flush()
    return row


def upsert_purchase_order(session: Session, po, proveedor_id: int,
                          saldo: Decimal | None) -> OrdenCompra:
    row = session.scalar(
        select(OrdenCompra).where(OrdenCompra.numero_oc == po.po_id))
    if row is None:
        row = OrdenCompra(numero_oc=po.po_id)
        session.add(row)
    row.proveedor_id = proveedor_id
    row.moneda = po.currency
    row.importe_autorizado = po.amount_authorized
    row.saldo = saldo if saldo is not None else po.amount_authorized
    row.estado = po.status
    row.vigencia_desde = po.valid_from
    row.vigencia_hasta = po.valid_to
    row.gl_account = po.gl_account
    row.mgmt_category = po.mgmt_category
    row.project_code = po.project_code
    session.flush()
    # lineas: reemplazo completo (idempotente)
    session.execute(delete(OrdenCompraLinea).where(OrdenCompraLinea.orden_id == row.id))
    for ln in po.lines:
        session.add(OrdenCompraLinea(orden_id=row.id, line_id=ln.line_id,
                                     descripcion=ln.description, importe=ln.amount))
    session.flush()
    return row


def _ruta_ap(inv: Invoice, tipo: str) -> str:
    if tipo == DOC_PROFORMA:
        return "anticipo"
    if tipo == DOC_OTHER:
        return "otro"
    return "po" if inv.po_ref else "non_po"


def upsert_documento_factura(session: Session, inv: Invoice, proveedor_id: int,
                             estado_operativo: str | None,
                             nivel_confianza: Decimal | None) -> Documento:
    tipo, _ = classify_document(inv)
    doc = session.scalar(
        select(Documento).where(Documento.id_interno == inv.invoice_id))
    if doc is None:
        doc = Documento(id_interno=inv.invoice_id)
        session.add(doc)
    doc.tipo_documental = tipo
    doc.origen = "synthetic"
    doc.fecha_recepcion = inv.received_date
    doc.estado_procesamiento = estado_operativo or "recibido"
    doc.fase_ciclo_vida = phase_for_status(estado_operativo)
    doc.mime_type = "application/pdf"
    doc.cantidad_paginas = 1
    session.flush()

    fac = session.scalar(select(Factura).where(Factura.documento_id == doc.id))
    if tipo == DOC_OTHER:
        return doc  # documentos 'other' no crean fila de factura
    if fac is None:
        fac = Factura(documento_id=doc.id)
        session.add(fac)
    fac.proveedor_id = proveedor_id
    fac.numero_factura = inv.invoice_number
    fac.fecha_emision = inv.issue_date
    fac.moneda = inv.currency
    fac.importe_total = inv.amount_total
    fac.importe_pendiente = inv.amount_total
    fac.referencia_orden = inv.po_ref
    fac.referencia_proyecto = inv.project_code
    fac.ruta_ap = _ruta_ap(inv, tipo)
    fac.metodo_pago = inv.metodo_pago
    fac.tratamiento_iva = inv.tratamiento_iva
    fac.nivel_confianza = nivel_confianza
    fac.estado_operativo = estado_operativo
    fac.iban_en_factura = inv.iban_on_invoice
    session.flush()
    return doc


# ------------------------------------------------------------------ auditoria
def persist_audit_trail(session: Session, audit: AuditTrail) -> int:
    """Persiste la cadena de auditoria (append-only). Revalida la cadena antes
    de insertar: si esta rota, NO escribe nada."""
    if not audit.verify_chain():
        raise ValueError("cadena de auditoria rota: no se persiste")
    session.execute(delete(AuditoriaEvento).where(AuditoriaEvento.run_id == audit.run_id))
    for ev in audit.events:
        session.add(AuditoriaEvento(
            run_id=ev.run_id, seq=ev.seq, commit=ev.commit, ts=ev.ts,
            actor=ev.agent, accion=ev.action,
            entidad_tipo=("factura" if ev.invoice_id else None),
            entidad_id=ev.invoice_id, invoice_id=ev.invoice_id,
            control_id=ev.control_id, resultado=ev.result,
            correlation_id=ev.invoice_id,
            evidencia=ev.evidence, prev_hash=ev.prev_hash, hash=ev.hash))
    session.flush()
    return len(audit.events)


# ------------------------------------------------------------------ orquestador
def _purge_run_scoped(session: Session, dataset: Dataset) -> None:
    """Limpia filas de corrida asociadas a los documentos del dataset, para que
    persistir dos veces la misma corrida sea idempotente."""
    ids = [i.invoice_id for i in dataset.invoices]
    if not ids:
        return
    docs = session.scalars(
        select(Documento.id).where(Documento.id_interno.in_(ids))).all()
    if docs:
        session.execute(delete(ControlEjecutado).where(ControlEjecutado.documento_id.in_(docs)))
        session.execute(delete(Excepcion).where(Excepcion.documento_id.in_(docs)))
        session.execute(delete(RevisionHumana).where(RevisionHumana.documento_id.in_(docs)))
    session.flush()


def persist_run(session: Session, dataset: Dataset, result: RunResult,
                audit: AuditTrail) -> dict[str, int]:
    """Persiste una corrida completa del motor. Idempotente por run_id/claves.

    Devuelve un resumen con conteos por tabla (util para tests/observabilidad).
    """
    # maestros
    vendor_pk: dict[str, int] = {}
    for vid, vendor in dataset.vendors.items():
        vendor_pk[vid] = upsert_vendor(session, vendor).id
    for po in dataset.pos.values():
        upsert_purchase_order(session, po, vendor_pk[po.vendor_id], saldo=None)

    _purge_run_scoped(session, dataset)

    # documentos + facturas + controles + excepciones + revisiones
    doc_pk: dict[str, int] = {}
    n_controls = n_exc = n_rev = 0
    conf_by_inv: dict[str, Decimal | None] = {}
    for inv in dataset.invoices:
        outcome = result.outcomes.get(inv.invoice_id)
        estado = outcome.status if outcome else None
        doc = upsert_documento_factura(
            session, inv, vendor_pk[inv.vendor_id], estado, conf_by_inv.get(inv.invoice_id))
        doc_pk[inv.invoice_id] = doc.id
        if outcome:
            for res in outcome.control_results:
                resultado = ("pasa" if res.passed
                             else ("falla-hard" if res.severity == "hard" else "flag-soft"))
                session.add(ControlEjecutado(
                    documento_id=doc.id, control_id=res.control_id,
                    passed=res.passed, resultado=resultado, severidad=res.severity,
                    detalle=res.detail, evidencia=res.evidence, checker=res.checker,
                    correlation_id=inv.invoice_id))
                n_controls += 1

    for exc in result.exceptions:
        doc_id = doc_pk.get(exc.invoice_id)
        if doc_id is None:
            continue
        session.add(Excepcion(
            documento_id=doc_id, control_id=exc.control_id, severidad=exc.severity,
            owner=exc.owner, detalle=exc.detail, evidencia=exc.evidence,
            fraud_alert=exc.fraud_alert, estado_resolucion="abierta"))
        n_exc += 1

    for ret in result.retenciones:
        doc_id = doc_pk.get(ret.invoice_id)
        if doc_id is None:
            continue
        session.add(RevisionHumana(
            documento_id=doc_id, campo_original=None, valor_extraido=None,
            valor_corregido=None, motivo_correccion=ret.detail,
            decision="propuesta_pendiente",
            evidencia={"motivo": ret.reason, "faltante": ret.missing,
                       "propuesta_del_agente": ret.propuesta}))
        n_rev += 1

    # lotes de pago (estado propuesto: el gate corre en la capa de UI/estados).
    # Borro hijos explicitamente (los delete() masivos no cascada relaciones
    # ORM y no todos los dialectos fuerzan FK ondelete) antes de borrar el lote.
    lote_ids = session.scalars(
        select(LotePago.id).where(LotePago.run_id == result.run_id)).all()
    if lote_ids:
        session.execute(delete(LoteFactura).where(LoteFactura.lote_id.in_(lote_ids)))
        session.execute(delete(Aprobacion).where(Aprobacion.lote_id.in_(lote_ids)))
        session.execute(delete(Pago).where(Pago.lote_id.in_(lote_ids)))
        session.execute(delete(LotePago).where(LotePago.id.in_(lote_ids)))
    session.flush()
    n_batch = 0
    for b in result.batches:
        lote = LotePago(fecha_lote=b.batch_date, estado="propuesto",
                        total=b.total, run_id=result.run_id)
        session.add(lote)
        session.flush()
        for inv_id in b.invoice_ids:
            fac = session.scalar(
                select(Factura).join(Documento).where(Documento.id_interno == inv_id))
            if fac is not None:
                session.add(LoteFactura(lote_id=lote.id, factura_id=fac.id))
        n_batch += 1

    n_audit = persist_audit_trail(session, audit)
    return {"proveedores": len(vendor_pk), "ordenes": len(dataset.pos),
            "documentos": len(doc_pk), "controles": n_controls,
            "excepciones": n_exc, "revisiones": n_rev, "lotes": n_batch,
            "auditoria": n_audit}


# ------------------------------------------------------------------ lecturas seguras
def masked_invoice_view(fac: Factura) -> dict:
    """Proyeccion de factura con datos bancarios ENMASCARADOS (UI/logs)."""
    return {
        "id_interno": fac.documento.id_interno if fac.documento else None,
        "numero_factura": fac.numero_factura,
        "importe_total": str(fac.importe_total),
        "moneda": fac.moneda,
        "estado_operativo": fac.estado_operativo,
        "ruta_ap": fac.ruta_ap,
        "metodo_pago": fac.metodo_pago,
        "iban_en_factura": masking.mask_iban(fac.iban_en_factura),
    }


def masked_vendor_view(prov: Proveedor) -> dict:
    """Proyeccion de proveedor con IBAN y tax_id ENMASCARADOS."""
    return {
        "codigo_interno": prov.codigo_interno,
        "nombre_legal": prov.nombre_legal,
        "tax_id": masking.mask_tax_id(prov.tax_id),
        "estado": prov.estado,
        "iban_autorizado": masking.mask_iban(prov.iban_autorizado),
        "banco_autorizado": prov.banco_autorizado,
    }


def verify_persisted_chain(session: Session, run_id: str) -> bool:
    """Revalida la cadena de hash persistida RECOMPUTANDO cada hash.

    Reconstruye el AuditEvent del dominio desde cada fila y compara su hash
    recalculado con el almacenado (detecta tampering, no solo rupturas de
    enlace). Equivalente a AuditTrail.verify_chain sobre la base.
    """
    from ..audit import AuditEvent, AuditTrail as _AT
    rows: Iterable[AuditoriaEvento] = session.scalars(
        select(AuditoriaEvento).where(AuditoriaEvento.run_id == run_id)
        .order_by(AuditoriaEvento.seq)).all()
    prev = _AT.GENESIS
    for ev in rows:
        rebuilt = AuditEvent(
            seq=ev.seq, ts=ev.ts, run_id=ev.run_id, commit=ev.commit or "",
            agent=ev.actor, action=ev.accion, invoice_id=ev.invoice_id,
            control_id=ev.control_id, result=ev.resultado,
            evidence=ev.evidencia or {}, prev_hash=ev.prev_hash)
        if ev.prev_hash != prev or rebuilt.compute_hash() != ev.hash:
            return False
        prev = ev.hash
    return True


def append_chained_event(
    session: Session, run_id: str, actor: str, accion: str,
    *, commit: str = "", invoice_id: str | None = None,
    control_id: str | None = None, result: str | None = None,
    evidencia: dict | None = None, entidad_tipo: str | None = None,
    entidad_id: str | None = None, estado_anterior: str | None = None,
    estado_posterior: str | None = None, correlation_id: str | None = None,
) -> AuditoriaEvento:
    """Anexa un evento a la cadena de auditoria de ``run_id`` (append-only).

    Calcula seq y prev_hash desde el ultimo evento del run y computa el hash con
    la misma logica del dominio, de modo que la cadena mixta (corrida + eventos
    vivos) verifique con ``verify_persisted_chain``. Nunca actualiza/borra.
    """
    from datetime import datetime, timezone

    from ..audit import AuditEvent, AuditTrail as _AT

    last = session.scalar(
        select(AuditoriaEvento).where(AuditoriaEvento.run_id == run_id)
        .order_by(AuditoriaEvento.seq.desc()).limit(1))
    seq = (last.seq + 1) if last else 1
    prev_hash = last.hash if last else _AT.GENESIS
    evidence = dict(evidencia or {})
    # los estados entran al payload hasheado (cubre tampering de esas columnas)
    if estado_anterior is not None:
        evidence.setdefault("estado_anterior", estado_anterior)
    if estado_posterior is not None:
        evidence.setdefault("estado_posterior", estado_posterior)
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    dom_ev = AuditEvent(
        seq=seq, ts=ts, run_id=run_id, commit=commit, agent=actor, action=accion,
        invoice_id=invoice_id, control_id=control_id, result=result,
        evidence=evidence, prev_hash=prev_hash)
    dom_ev.hash = dom_ev.compute_hash()

    row = AuditoriaEvento(
        run_id=run_id, seq=seq, commit=commit, ts=dom_ev.ts, actor=actor,
        accion=accion, entidad_tipo=entidad_tipo or ("factura" if invoice_id else None),
        entidad_id=entidad_id or invoice_id, invoice_id=invoice_id,
        control_id=control_id, resultado=result,
        estado_anterior=estado_anterior, estado_posterior=estado_posterior,
        correlation_id=correlation_id or invoice_id, evidencia=evidence,
        prev_hash=prev_hash, hash=dom_ev.hash)
    session.add(row)
    session.flush()
    return row
