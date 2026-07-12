"""Revisión humana aplicada a documentos reales del Trial."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ..components import extraction_view as ev
from . import session as sess
from . import workflow
from .workflow_ui import active_session_or_resume


FIELD_LABELS = {
    "document_type": "Tipo documental",
    "proveedor_nombre_comercial": "Proveedor",
    "numero_factura": "Número de factura",
    "fecha_emision": "Fecha de emisión",
    "fecha_vencimiento_calculada": "Fecha de vencimiento",
    "moneda": "Moneda",
    "importe_total": "Importe total",
    "po_reference": "Referencia de OC (opcional)",
}


def _value(document: dict, field: str) -> str:
    value = document.get(field)
    return "" if value is None else str(value)


def _render_next_step() -> None:
    if st.button("Aprobación - propuesta de pago", type="primary",
                 use_container_width=True, key="trial_review_next_payment"):
        from .shell import PAYMENT_APPROVAL

        st.session_state["_trial_navigation"] = PAYMENT_APPROVAL
        st.rerun()


def render() -> None:
    st.markdown("## Revisión humana")
    st.caption("El agente deriva únicamente los documentos que necesitan juicio humano. "
               "Una factura sin OC no se deriva por ese único motivo.")
    session = active_session_or_resume("trial_review")
    if session is None:
        return

    queue = workflow.review_queue(session.results, session.review_decisions)
    pending = [item for item in queue if item["pending"]]
    confirmed = sum(1 for item in queue
                    if item["decision"].get("status") == "confirmed")
    retained = sum(1 for item in queue
                   if item["decision"].get("status") == "retained")
    c1, c2, c3 = st.columns(3)
    c1.metric("Pendientes de revisión", len(pending))
    c2.metric("Confirmadas", confirmed)
    c3.metric("Retenidas", retained)

    if not queue:
        st.success("No hay documentos que requieran revisión humana.")
        _render_next_step()
        return

    rows = []
    for item in queue:
        result = item["result"]
        decision = item["decision"]
        rows.append({
            "archivo": result.doc_id,
            "proveedor": result.document.get("proveedor_nombre_comercial") or "—",
            "número": result.document.get("numero_factura") or "—",
            "motivo": " · ".join(item["reasons"]) or "revisión resuelta",
            "estado": {"confirmed": "Confirmada", "retained": "Retenida"}.get(
                decision.get("status"), "Pendiente"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    for item in queue:
        result = item["result"]
        decision = item["decision"]
        status = {"confirmed": "Confirmada", "retained": "Retenida"}.get(
            decision.get("status"), "Pendiente")
        with st.expander(f"{result.doc_id} · {status}", expanded=False):
            if item["reasons"]:
                st.warning("Motivos: " + " · ".join(item["reasons"]))
            if decision:
                st.success(f"Última decisión: {status} por {decision.get('actor', '—')} · "
                           f"{decision.get('timestamp', '—')}")
            with st.form(
                    f"trial_review_form_{session.audit.run_id}_{result.doc_id}"):
                updates = {}
                left, right = st.columns(2)
                for index, field in enumerate(workflow.EDITABLE_FIELDS):
                    container = left if index % 2 == 0 else right
                    if field == "document_type":
                        options = ["invoice", "proforma_or_advance_request", "other"]
                        current = _value(result.document, field)
                        updates[field] = container.selectbox(
                            FIELD_LABELS[field], options,
                            index=options.index(current) if current in options else 2,
                            key=f"review_{result.doc_id}_{field}")
                    else:
                        updates[field] = container.text_input(
                            FIELD_LABELS[field], value=_value(result.document, field),
                            key=f"review_{result.doc_id}_{field}")
                reviewer = st.text_input("Revisado por", key=f"reviewer_{result.doc_id}")
                note = st.text_area("Comentario / evidencia de la decisión",
                                    key=f"review_note_{result.doc_id}")
                confirm = st.form_submit_button(
                    "Confirmar revisión", type="primary", use_container_width=True)
                retain = st.form_submit_button(
                    "Retener documento", use_container_width=True)
            try:
                if confirm:
                    sess.confirm_review(session, result.doc_id, reviewer, updates, note)
                    sess.persist(session)
                    st.success("Revisión confirmada y auditada.")
                    st.rerun()
                if retain:
                    sess.retain_review(session, result.doc_id, reviewer, note)
                    sess.persist(session)
                    st.success("Documento retenido y decisión auditada.")
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.markdown("### Audit trail de la corrida")
    ev.render_session_audit(session.audit, persisted=sess.persistence_available())
    st.markdown("---")
    _render_next_step()
