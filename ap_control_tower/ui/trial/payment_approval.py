"""Aprobación humana para propuesta de pago; nunca libera dinero al banco."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO

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

PAYMENT_EXPORT_COLUMNS = [
    "beneficiario", "tax_id_proveedor", "factura_documento", "fecha_emision",
    "vencimiento", "moneda", "importe", "iban_cuenta", "bic_swift",
    "banco", "metodo_pago", "referencia_oc", "referencia_proyecto",
    "tipo_documental", "aprobado_por", "fecha_aprobacion", "estado",
]


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


def payment_export_rows(approved_rows: list[dict]) -> list[dict]:
    rows = []
    for row in approved_rows:
        result = row["result"]
        doc = result.document
        decision = row.get("decision") or {}
        rows.append({
            "beneficiario": (doc.get("proveedor_razon_social_legal") or
                              doc.get("proveedor_nombre_comercial") or ""),
            "tax_id_proveedor": doc.get("proveedor_tax_id") or "",
            "factura_documento": doc.get("numero_factura") or result.doc_id,
            "fecha_emision": doc.get("fecha_emision") or "",
            "vencimiento": (doc.get("fecha_vencimiento_calculada") or
                            doc.get("fecha_vencimiento_texto") or ""),
            "moneda": doc.get("moneda") or "",
            "importe": doc.get("importe_total"),
            "iban_cuenta": (doc.get("iban") or
                             doc.get("proveedor_cuenta_bancaria") or ""),
            "bic_swift": doc.get("bic") or "",
            "banco": doc.get("proveedor_banco") or "",
            "metodo_pago": doc.get("metodo_pago") or "",
            "referencia_oc": doc.get("po_reference") or "",
            "referencia_proyecto": doc.get("project_reference") or "",
            "tipo_documental": doc.get("document_type") or "",
            "aprobado_por": decision.get("actor") or "",
            "fecha_aprobacion": decision.get("timestamp") or "",
            "estado": "aprobada_para_propuesta",
        })
    return rows


def payment_export_csv(approved_rows: list[dict]) -> bytes:
    frame = pd.DataFrame(payment_export_rows(approved_rows),
                         columns=PAYMENT_EXPORT_COLUMNS)
    return frame.to_csv(index=False).encode("utf-8-sig")


def payment_export_excel(approved_rows: list[dict]) -> bytes:
    frame = pd.DataFrame(payment_export_rows(approved_rows),
                         columns=PAYMENT_EXPORT_COLUMNS)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Propuesta de pago", index=False)
        sheet = writer.book["Propuesta de pago"]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = cell.font.copy(bold=True)
        for cells in sheet.columns:
            width = min(42, max(12, max(len(str(cell.value or "")) for cell in cells) + 2))
            sheet.column_dimensions[cells[0].column_letter].width = width
    return output.getvalue()


def _go_to_human_review() -> None:
    from .shell import HUMAN_REVIEW

    st.session_state["_trial_navigation"] = HUMAN_REVIEW
    st.rerun()


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

    if approved:
        st.markdown("### Exportar lote aprobado")
        st.info("Export operativo para Tesorería. No ejecuta pagos ni reemplaza el "
                "formato, la firma ni las validaciones exigidas por el banco.")
        export_csv, export_excel = st.columns(2)
        export_csv.download_button(
            "Exportar lote aprobado CSV", data=payment_export_csv(approved),
            file_name="ap-control-tower-propuesta-pago.csv", mime="text/csv",
            use_container_width=True,
            key=f"trial_payment_export_csv_{session.audit.run_id}")
        export_excel.download_button(
            "Exportar lote aprobado Excel", data=payment_export_excel(approved),
            file_name="ap-control-tower-propuesta-pago.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"trial_payment_export_xlsx_{session.audit.run_id}")
        if any(bool(row["result"].document.get("iban_enmascarado")) for row in approved):
            st.warning("El lote contiene datos bancarios enmascarados. Tesorería debe "
                       "completarlos y validarlos antes de preparar el archivo bancario.")

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
        st.info("Estos documentos se gestionan en Revisión humana. Allí pueden "
                "reclasificarse, retenerse o autorizarse excepcionalmente para pago.")
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
        if st.button("Ir a Revisión humana", type="primary", use_container_width=True,
                     key="trial_payment_go_review"):
            _go_to_human_review()

    st.markdown("### Audit trail de la corrida")
    ev.render_session_audit(session.audit, persisted=sess.persistence_available())
