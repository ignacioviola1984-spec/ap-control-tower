"""Aprobación humana para propuesta de pago; nunca libera dinero al banco."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st

from ..components import extraction_view as ev
from . import session as sess
from . import workflow
from .workflow_ui import active_session_or_resume


STATUS_LABEL = {
    "eligible": "Elegible",
    "retained": "Retenida",
    "approved": "Aprobada para propuesta",
    "rejected": "Rechazada",
}


def _totals(rows: list[dict], status: str) -> str:
    totals: dict[str, Decimal] = {}
    for row in rows:
        if row["status"] != status:
            continue
        doc = row["result"].document
        try:
            amount = Decimal(str(doc.get("importe_total")))
        except (InvalidOperation, TypeError):
            continue
        currency = str(doc.get("moneda") or "—")
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    return " · ".join(f"{currency} {amount:,.2f}" for currency, amount in totals.items()) or "—"


def render() -> None:
    st.markdown("## Aprobación para propuesta de pago")
    st.info("Esta decisión prepara una propuesta controlada. No contabiliza, no genera "
            "un archivo bancario y no libera dinero.")
    session = active_session_or_resume("trial_payment")
    if session is None:
        return

    rows = workflow.approval_rows(
        session.results, session.review_decisions, session.approval_decisions)
    eligible = [row for row in rows if row["status"] == "eligible"]
    approved = [row for row in rows if row["status"] == "approved"]
    retained = [row for row in rows if row["status"] == "retained"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Elegibles", len(eligible), _totals(rows, "eligible"))
    c2.metric("Aprobadas para propuesta", len(approved), _totals(rows, "approved"))
    c3.metric("Retenidas", len(retained))

    table = []
    for row in rows:
        result = row["result"]
        doc = result.document
        table.append({
            "archivo": result.doc_id,
            "proveedor": doc.get("proveedor_nombre_comercial") or "—",
            "número": doc.get("numero_factura") or "—",
            "vencimiento": doc.get("fecha_vencimiento_calculada") or
                           doc.get("fecha_vencimiento_texto") or "—",
            "moneda": doc.get("moneda") or "—",
            "total": doc.get("importe_total") or "—",
            "estado": STATUS_LABEL[row["status"]],
            "motivo de retención": " · ".join(row["reasons"]) or "—",
        })
    st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)

    selectable = [row for row in rows if row["status"] in {"eligible", "retained"}]
    labels = {
        f"{row['result'].doc_id} · "
        f"{row['result'].document.get('proveedor_nombre_comercial') or '—'} · "
        f"{row['result'].document.get('moneda') or '—'} "
        f"{row['result'].document.get('importe_total') or '—'}": row
        for row in selectable
    }
    with st.form("trial_payment_decision"):
        selected_labels = st.multiselect("Facturas para decidir", list(labels))
        approver = st.text_input("Decisión tomada por")
        note = st.text_area("Comentario / motivo")
        acknowledgement = st.checkbox(
            "Confirmo que esta acción no libera dinero ni reemplaza la autorización bancaria")
        approve = st.form_submit_button(
            "Aprobar para propuesta de pago", type="primary", use_container_width=True)
        reject = st.form_submit_button("Rechazar selección", use_container_width=True)

    selected_ids = [labels[label]["result"].doc_id for label in selected_labels]
    try:
        if approve:
            if not acknowledgement:
                raise ValueError("Confirmá el alcance de la acción antes de aprobar.")
            sess.decide_payment_proposal(
                session, selected_ids, approver, "approved", note)
            sess.persist(session)
            st.success("Facturas aprobadas para la propuesta de pago. No se liberó dinero.")
            st.rerun()
        if reject:
            sess.decide_payment_proposal(
                session, selected_ids, approver, "rejected", note)
            sess.persist(session)
            st.success("Selección rechazada y auditada.")
            st.rerun()
    except ValueError as exc:
        st.error(str(exc))

    st.markdown("### Audit trail de la corrida")
    ev.render_session_audit(session.audit, persisted=sess.persistence_available())
