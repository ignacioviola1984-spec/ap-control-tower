"""Revisión humana y gate separado de propuesta de pago."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .pilot_format import (
    STATE_LABELS,
    format_amount,
    format_totals,
    format_datetime,
    label_for_code,
    priority_for,
    supplier_name,
    totals_by_currency,
)
from .pilot_pages_common import (
    active_session_or_resume,
    metric_row,
    page_header,
    render_document_detail,
    result_by_id,
)
from .trial import payment_approval
from .trial import session as sess
from .trial import workflow


FIELD_LABELS = {
    "document_type": "Tipo documental",
    "proveedor_nombre_comercial": "Proveedor",
    "numero_factura": "Número de factura",
    "fecha_emision": "Fecha de emisión (aaaa-mm-dd)",
    "fecha_vencimiento_calculada": "Fecha de vencimiento (aaaa-mm-dd)",
    "moneda": "Moneda",
    "importe_total": "Importe total",
    "po_reference": "Referencia de OC",
}


@st.dialog(
    "Confirmar retención",
    width="medium",
    icon=":material/pause_circle:",
    on_dismiss="rerun",
)
def _confirm_retention(active, doc_id: str, actor: str, note: str) -> None:
    result = result_by_id(active, doc_id)
    st.write(f"Se retendrá **{doc_id} · {supplier_name(result.document)}**.")
    st.write("El documento no podrá avanzar a la propuesta de pago hasta una nueva decisión.")
    st.write(f"Responsable registrado: **{actor or 'Sin informar'}**.")
    st.write(f"Motivo: **{note or 'Sin informar'}**.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Cancelar", key="retain_cancel"):
            st.rerun()
        if st.button(
            "Retener documento",
            type="primary",
            icon=":material/pause_circle:",
            key="retain_confirm",
        ):
            try:
                sess.retain_review(active, doc_id, actor, note)
            except ValueError as exc:
                st.error(str(exc))
                return
            sess.persist(active)
            st.rerun()


@st.dialog(
    "Confirmar excepción",
    width="medium",
    icon=":material/gavel:",
    on_dismiss="rerun",
)
def _confirm_exception(active, doc_id: str, actor: str, note: str) -> None:
    result = result_by_id(active, doc_id)
    st.write(f"Se registrará una excepción para **{doc_id} · {supplier_name(result.document)}**.")
    st.warning(
        "La excepción habilita la evaluación en Lote de pago, pero no aprueba "
        "el documento ni libera dinero."
    )
    st.write(f"Responsable registrado: **{actor or 'Sin informar'}**.")
    st.write(f"Motivo: **{note or 'Sin informar'}**.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Cancelar", key="exception_cancel"):
            st.rerun()
        if st.button(
            "Registrar excepción",
            type="primary",
            icon=":material/gavel:",
            key="exception_confirm",
        ):
            try:
                sess.approve_payment_exception(active, doc_id, actor, note)
            except ValueError as exc:
                st.error(str(exc))
                return
            sess.persist(active)
            st.rerun()


@st.dialog(
    "Confirmar decisión sobre la propuesta",
    width="medium",
    icon=":material/verified_user:",
    on_dismiss="rerun",
)
def _confirm_payment_decision(active, doc_ids: list[str], actor: str,
                              note: str, status: str) -> None:
    results = [result_by_id(active, doc_id) for doc_id in doc_ids]
    action = {
        "approved": "aprobar para la propuesta de pago",
        "rejected": "rechazar para la propuesta de pago",
        "excluded": "excluir de la propuesta de pago",
    }[status]
    st.write(f"Se va a **{action}** {len(results)} documento(s).")
    st.write(f"Total afectado: **{format_totals(totals_by_currency(results))}**.")
    st.write(f"Responsable registrado: **{actor or 'Sin informar'}**.")
    if note:
        st.write(f"Motivo: **{note}**.")
    if status == "approved":
        st.info(
            "La decisión incorpora documentos a una propuesta controlada. No contabiliza, "
            "no genera un archivo bancario y no libera dinero."
        )
    else:
        st.warning("Los documentos quedarán fuera de la propuesta hasta una nueva decisión.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Cancelar", key="payment_cancel"):
            st.rerun()
        if st.button(
            "Confirmar decisión",
            type="primary",
            icon=":material/check:",
            key="payment_confirm",
        ):
            try:
                sess.decide_payment_proposal(active, doc_ids, actor, status, note)
            except ValueError as exc:
                st.error(str(exc))
                return
            sess.persist(active)
            st.rerun()


def render_human_review() -> None:
    page_header(
        "Revisión humana",
        "Cola priorizada de documentos que necesitan juicio, evidencia o corrección.",
    )
    active = active_session_or_resume("review")
    if active is None:
        return
    queue = workflow.review_queue(
        active.results, active.review_decisions, active.approval_decisions
    )
    pending = [item for item in queue if item["pending"]]
    confirmed = sum(
        1 for item in queue
        if item["decision"].get("status") in {"confirmed", "payment_exception_approved"}
    )
    retained = sum(
        1 for item in queue if item["decision"].get("status") == "retained"
    )
    metric_row(
        [
            ("Pendientes", len(pending)),
            ("Confirmados o autorizados", confirmed),
            ("Retenidos", retained),
        ]
    )

    if not queue:
        st.success(
            "No hay documentos pendientes de revisión humana.",
            icon=":material/check_circle:",
        )
        return

    ordered = []
    for item in queue:
        priority_rank, priority = priority_for(item["reasons"])
        result = item["result"]
        ordered.append((priority_rank, {
            "doc_id": result.doc_id,
            "Prioridad": priority,
            "Documento": result.doc_id,
            "Proveedor": supplier_name(result.document),
            "Número": result.document.get("numero_factura") or "—",
            "Motivo": " · ".join(item["reasons"]) or "Decisión registrada",
            "Estado": (
                "Pendiente" if item["pending"]
                else label_for_code(item["decision"].get("status") or "Resuelto")
            ),
            "item": item,
        }))
    ordered.sort(key=lambda value: (value[0], value[1]["Proveedor"].casefold()))
    rows = [value[1] for value in ordered]
    frame = pd.DataFrame(
        [{key: row[key] for key in ("Prioridad", "Documento", "Proveedor", "Número", "Motivo", "Estado")}
         for row in rows]
    )
    event = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=320,
        on_select="rerun",
        selection_mode="single-row-required",
        selection_default={"selection": {"rows": [0]}},
        key="review_queue",
        column_config={
            "Documento": st.column_config.TextColumn("Documento", pinned=True),
            "Proveedor": st.column_config.TextColumn("Proveedor", pinned=True),
        },
    )
    selected_index = list(event.selection.rows)[0]
    selected = rows[selected_index]
    item = selected["item"]
    result = item["result"]

    render_document_detail(active, result)
    st.subheader("Registrar revisión")
    if item["reasons"]:
        st.warning("Motivo de derivación: " + " · ".join(item["reasons"]))
    decision = item["decision"]
    if decision:
        st.caption(
            "Última decisión: "
            + label_for_code(decision.get("status"))
            + " · "
            + str(decision.get("actor") or "—")
            + " · "
            + format_datetime(decision.get("timestamp"))
        )

    with st.form(f"review_form_{active.audit.run_id}_{result.doc_id}"):
        updates = {}
        fields = st.columns(2)
        for index, field in enumerate(workflow.EDITABLE_FIELDS):
            target = fields[index % 2]
            current = result.document.get(field)
            if field == "document_type":
                options = ["invoice", "proforma_or_advance_request", "other"]
                updates[field] = target.selectbox(
                    FIELD_LABELS[field],
                    options,
                    index=options.index(current) if current in options else 2,
                    format_func=lambda value: {
                        "invoice": "Factura fiscal",
                        "proforma_or_advance_request": "Proforma o anticipo",
                        "other": "Otro documento",
                    }[value],
                )
            else:
                updates[field] = target.text_input(
                    FIELD_LABELS[field],
                    value="" if current is None else str(current),
                )
        actor = st.text_input("Responsable", placeholder="Nombre y apellido")
        note = st.text_area(
            "Nota o evidencia",
            help="Es obligatoria para retenciones y excepciones.",
        )
        with st.container(horizontal=True):
            confirm = st.form_submit_button(
                "Confirmar datos",
                type="primary",
                icon=":material/check_circle:",
            )
            retain = st.form_submit_button(
                "Retener",
                icon=":material/pause_circle:",
            )
            exception = st.form_submit_button(
                "Registrar excepción",
                icon=":material/gavel:",
                disabled=result.document.get("document_type") == "invoice",
            )

    if confirm:
        try:
            sess.confirm_review(active, result.doc_id, actor, updates, note)
        except ValueError as exc:
            st.error(str(exc))
        else:
            sess.persist(active)
            st.toast("Datos confirmados y decisión auditada.", icon=":material/check:")
            st.rerun()
    elif retain:
        _confirm_retention(active, result.doc_id, actor, note)
    elif exception:
        _confirm_exception(active, result.doc_id, actor, note)


def render_payment_proposal() -> None:
    page_header(
        "Lote de pago",
        "Gate humano separado de la confirmación documental.",
    )
    st.info(
        "Las decisiones de esta página preparan una propuesta controlada. No contabilizan "
        "ni liberan dinero."
    )
    active = active_session_or_resume("payment")
    if active is None:
        return
    rows = workflow.approval_rows(
        active.results, active.review_decisions, active.approval_decisions
    )
    eligible = [row for row in rows if row["status"] == "eligible"]
    approved = [row for row in rows if row["status"] == "approved"]
    retained = [
        row for row in rows if row["status"] in {"retained", "rejected", "excluded"}
    ]
    metric_row(
        [
            ("Elegibles", len(eligible)),
            ("Aprobados para propuesta", len(approved)),
            ("Retenidos o excluidos", len(retained)),
        ]
    )
    st.caption("Total elegible: " + format_totals(totals_by_currency(eligible)))
    st.caption("Total aprobado: " + format_totals(totals_by_currency(approved)))

    table_rows = []
    for row in rows:
        result = row["result"]
        document = result.document
        table_rows.append({
            "doc_id": result.doc_id,
            "Documento": result.doc_id,
            "Proveedor": supplier_name(document),
            "Número": document.get("numero_factura") or "—",
            "Vencimiento": document.get("fecha_vencimiento_calculada")
            or document.get("fecha_vencimiento_texto") or "—",
            "Importe": format_amount(document.get("importe_total"), document.get("moneda")),
            "Estado": STATE_LABELS.get(row["status"], row["status"]),
            "Motivo": " · ".join(row["reasons"]) or "—",
            "row": row,
        })
    frame = pd.DataFrame(
        [{key: item[key] for key in ("Documento", "Proveedor", "Número", "Vencimiento", "Importe", "Estado", "Motivo")}
         for item in table_rows]
    )
    event = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=360,
        on_select="rerun",
        selection_mode="multi-row",
        key="payment_documents",
        column_config={
            "Documento": st.column_config.TextColumn("Documento", pinned=True),
            "Proveedor": st.column_config.TextColumn("Proveedor", pinned=True),
        },
    )
    selected_indexes = list(event.selection.rows)
    selected_items = [table_rows[index] for index in selected_indexes]
    selected_ids = [item["doc_id"] for item in selected_items]
    can_approve = bool(selected_items) and all(
        item["row"]["status"] == "eligible" for item in selected_items
    )
    if not selected_items:
        st.caption("Seleccioná una o más filas para habilitar una decisión.")
    elif not can_approve:
        st.caption("Aprobar solo está disponible cuando todas las filas son elegibles.")

    with st.form(f"payment_decision_{active.audit.run_id}"):
        actor = st.text_input("Responsable de la decisión", placeholder="Nombre y apellido")
        note = st.text_area("Motivo o comentario")
        acknowledgement = st.checkbox(
            "Confirmo que la decisión no libera dinero ni reemplaza la autorización bancaria"
        )
        with st.container(horizontal=True):
            approve = st.form_submit_button(
                "Aprobar para propuesta",
                type="primary",
                icon=":material/verified:",
                disabled=not can_approve,
            )
            exclude = st.form_submit_button(
                "Excluir",
                icon=":material/block:",
                disabled=not selected_items,
            )
            reject = st.form_submit_button(
                "Rechazar",
                icon=":material/cancel:",
                disabled=not selected_items,
            )
    status = "approved" if approve else "excluded" if exclude else "rejected" if reject else None
    if status:
        if not actor.strip():
            st.error("Ingresá el nombre de la persona responsable de la decisión.")
        elif not acknowledgement:
            st.error("Confirmá el alcance de la acción antes de continuar.")
        elif status in {"excluded", "rejected"} and not note.strip():
            st.error("Ingresá el motivo de la exclusión o el rechazo.")
        else:
            _confirm_payment_decision(active, selected_ids, actor, note, status)

    if approved:
        st.subheader("Exportar propuesta aprobada")
        st.caption(
            "El archivo no ejecuta pagos. Los datos bancarios se entregan enmascarados."
        )
        export_row = st.columns(2)
        export_row[0].download_button(
            "Exportar CSV",
            data=payment_approval.payment_export_csv(approved),
            file_name="torre-control-propuesta-pago.csv",
            mime="text/csv",
            icon=":material/download:",
            width="stretch",
            key=f"payment_export_csv_{active.audit.run_id}",
        )
        try:
            excel_data = payment_approval.payment_export_excel(approved)
        except (ImportError, ModuleNotFoundError):
            export_row[1].caption(
                "La exportación Excel no está disponible en este entorno; usá el CSV."
            )
        else:
            export_row[1].download_button(
                "Exportar Excel",
                data=excel_data,
                file_name="torre-control-propuesta-pago.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                width="stretch",
                key=f"payment_export_excel_{active.audit.run_id}",
            )
