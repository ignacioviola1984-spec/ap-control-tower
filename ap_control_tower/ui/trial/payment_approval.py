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
    "excluded": "Fuera de propuesta",
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


def _selected_doc_ids(labels: dict, selected_labels: list[str],
                      select_all: bool) -> list[str]:
    """Resuelve la selección sin depender del estado visual del multiselect."""
    chosen = list(labels) if select_all else selected_labels
    return [labels[label]["result"].doc_id for label in chosen if label in labels]


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
    retained = [row for row in rows
                if row["status"] in {"retained", "rejected", "excluded"}]
    c1, c2, c3 = st.columns(3)
    c1.metric("Elegibles", len(eligible))
    c1.caption("Total elegible: " + _totals(rows, "eligible"))
    c2.metric("Aprobadas para propuesta", len(approved))
    c2.caption("Total aprobado: " + _totals(rows, "approved"))
    c3.metric("Retenidas / fuera de propuesta", len(retained))

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

    st.markdown("### Decisión sobre facturas elegibles")
    st.caption("Las facturas elegibles todavía no están aprobadas. Seleccionalas y "
               "ejecutá la aprobación humana para incorporarlas a la propuesta de pago.")
    labels = {
        f"{row['result'].doc_id} · "
        f"{row['result'].document.get('proveedor_nombre_comercial') or '—'} · "
        f"{row['result'].document.get('moneda') or '—'} "
        f"{row['result'].document.get('importe_total') or '—'}": row
        for row in eligible
    }
    if not labels:
        st.success("No quedan facturas elegibles pendientes de aprobación.")
    else:
        select_all = st.checkbox(
            "Seleccionar todas las elegibles",
            key=f"trial_payment_select_all_{session.audit.run_id}")
        with st.form(f"trial_payment_decision_{session.audit.run_id}"):
            selected_labels = st.multiselect(
                "Facturas elegibles para aprobar", list(labels),
                default=list(labels) if select_all else [],
                disabled=select_all,
                key=f"trial_payment_selection_{session.audit.run_id}")
            approver = st.text_input("Decisión tomada por")
            note = st.text_area("Comentario / motivo")
            acknowledgement = st.checkbox(
                "Confirmo que esta acción no libera dinero ni reemplaza la autorización bancaria")
            approve = st.form_submit_button(
                "Aprobar para propuesta de pago", type="primary", use_container_width=True)

        selected_ids = _selected_doc_ids(labels, selected_labels, select_all)
        try:
            if approve:
                if not acknowledgement:
                    raise ValueError("Confirmá el alcance de la acción antes de aprobar.")
                sess.decide_payment_proposal(
                    session, selected_ids, approver, "approved", note)
                sess.persist(session)
                st.success("Facturas aprobadas para la propuesta de pago. No se liberó dinero.")
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.markdown("### Documentos retenidos / fuera de la propuesta")
    if not retained:
        st.success("No hay documentos retenidos ni fuera de la propuesta.")
    else:
        retained_table = []
        for row in retained:
            result = row["result"]
            doc = result.document
            decision = row.get("decision") or {}
            retained_table.append({
                "archivo": result.doc_id,
                "proveedor": doc.get("proveedor_nombre_comercial") or "—",
                "tipo documental": doc.get("document_type") or "—",
                "número": doc.get("numero_factura") or "—",
                "moneda": doc.get("moneda") or "—",
                "importe": doc.get("importe_total") or "—",
                "motivo": " · ".join(row["reasons"]) or "—",
                "estado": STATUS_LABEL[row["status"]],
                "última decisión": (f"{decision.get('actor')} · "
                                    f"{decision.get('timestamp')}"
                                    if decision else "—"),
            })
        st.dataframe(pd.DataFrame(retained_table), use_container_width=True,
                     hide_index=True)

        for row in retained:
            result = row["result"]
            doc = result.document
            with st.expander(
                    f"{result.doc_id} · {STATUS_LABEL[row['status']]}", expanded=False):
                st.warning("Motivo: " + (" · ".join(row["reasons"]) or "—"))
                st.caption("Tipo documental: " + str(doc.get("document_type") or "—"))
                doc_events = [event for event in session.audit.events
                              if event.invoice_id == result.doc_id]
                if doc_events:
                    st.dataframe(pd.DataFrame([{
                        "hora (UTC)": event.ts, "acción": event.action,
                        "resultado": event.result or "",
                    } for event in doc_events[-8:]]), use_container_width=True,
                                 hide_index=True)
                with st.form(
                        f"trial_retained_action_{session.audit.run_id}_{result.doc_id}"):
                    actor = st.text_input(
                        "Decisión tomada por", key=f"retained_actor_{result.doc_id}")
                    retained_note = st.text_area(
                        "Motivo / evidencia", key=f"retained_note_{result.doc_id}")
                    exclude = st.form_submit_button(
                        "Confirmar exclusión de la propuesta", use_container_width=True)
                    reject = st.form_submit_button(
                        "Rechazar para propuesta", use_container_width=True)
                    request_review = st.form_submit_button(
                        "Enviar a revisión por clasificación", use_container_width=True)
                try:
                    if exclude:
                        sess.decide_payment_proposal(
                            session, [result.doc_id], actor, "excluded", retained_note)
                        sess.persist(session)
                        st.success("Exclusión confirmada y auditada.")
                        st.rerun()
                    if reject:
                        sess.decide_payment_proposal(
                            session, [result.doc_id], actor, "rejected", retained_note)
                        sess.persist(session)
                        st.success("Rechazo registrado y auditado.")
                        st.rerun()
                    if request_review:
                        sess.request_classification_review(
                            session, result.doc_id, actor, retained_note)
                        sess.persist(session)
                        st.success("Revisión por clasificación solicitada y auditada.")
                        st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.markdown("### Audit trail de la corrida")
    ev.render_session_audit(session.audit, persisted=sess.persistence_available())
