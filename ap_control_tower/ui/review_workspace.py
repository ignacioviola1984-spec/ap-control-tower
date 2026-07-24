"""Workspace de revisión humana: cola · documento · copiloto en tres zonas.

Se construye con elementos NATIVOS de Streamlit en vez de un componente JS a
medida. El spec admite este camino ("mantener un fallback nativo") y evita el
riesgo de sincronizar coordenadas del PDF: acá el visor y los campos van lado a
lado, y la vinculación campo→posición queda documentada como mejora futura.

Python sigue siendo la autoridad: toda validación, decisión, auditoría y
persistencia pasa por las funciones de sesión existentes. Esta capa solo
presenta y captura la interacción.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from . import design
from .agent_panel import render_document_agent
from .pilot_format import format_datetime, label_for_code, supplier_name
from .pilot_pages_common import render_pdf_viewer
from .trial import session as sess
from .trial import workflow


_FIELD_LABELS = {
    "document_type": "Tipo documental",
    "proveedor_nombre_comercial": "Proveedor",
    "numero_factura": "Número de factura",
    "fecha_emision": "Fecha de emisión (aaaa-mm-dd)",
    "fecha_vencimiento_calculada": "Vencimiento (aaaa-mm-dd)",
    "moneda": "Moneda",
    "importe_total": "Importe total",
    "po_reference": "Referencia de OC",
}
_DOC_TYPE_LABELS = {
    "invoice": "Factura fiscal",
    "proforma_or_advance_request": "Proforma o anticipo",
    "other": "Otro documento",
}

_STICKY_CSS = """
<style>
/* Barra de acciones fija al pie de la ventana durante la revisión. */
.st-key-ap_review_actions {
  position: sticky; bottom: 0; z-index: 20;
  background: #FFFFFF; border: 1px solid #DCE3ED; border-radius: 12px;
  padding: 10px 14px; box-shadow: 0 -2px 8px rgba(16,24,40,.06);
  margin-top: 8px;
}
.st-key-ap_review_queue [data-testid="stVerticalBlockBorderWrapper"] { margin-bottom: 6px; }
.ap-conf-low { color: #B42318; font-weight: 600; }
.ap-conf-mid { color: #9A5B00; font-weight: 600; }
</style>
"""


def _queue_state_key(active) -> str:
    return f"_ap_review_idx_{active.audit.run_id}"


def _confidence_flag(result, field: str) -> str:
    """Marca visual de confianza para un campo, sin inventar números."""
    value = (result.field_confidences or {}).get(field)
    if value is None:
        return ""
    try:
        conf = Decimal(str(value))
    except (TypeError, ValueError):
        return ""
    if conf < Decimal("0.60"):
        return '<span class="ap-conf-low">· confianza baja</span>'
    if conf < Decimal("0.80"):
        return '<span class="ap-conf-mid">· confianza media</span>'
    return ""


def _render_queue(active, ordered: list[dict], idx: int, state_key: str) -> None:
    st.markdown("##### Cola de revisión")
    st.caption(f"{len(ordered)} documento(s)")
    for position, row in enumerate(ordered):
        item = row["item"]
        _label, tone = design.priority_tone(item["reasons"])
        selected = position == idx
        with st.container(border=True):
            st.html(
                design.chip(row["Prioridad"], tone)
                + f'<span style="margin-left:6px;font-size:12px;color:#5A6B85;">'
                f'{row["Estado"]}</span>'
            )
            if st.button(
                f'{row["Proveedor"][:26]} · {row["Documento"]}',
                key=f"_ap_q_{state_key}_{position}",
                width="stretch",
                type="primary" if selected else "secondary",
            ):
                st.session_state[state_key] = position
                st.rerun()


def _render_document(active, result, item) -> dict:
    st.markdown("##### Documento y datos extraídos")
    if item["reasons"]:
        for reason in item["reasons"]:
            _l, tone = design.priority_tone([reason])
            design.alert(reason, tone=tone if tone != "muted" else "info",
                         title="Motivo de derivación")

    missing = set(workflow.missing_critical_fields(result.document))
    updates: dict = {}
    fields = st.columns(2)
    for index, field in enumerate(workflow.EDITABLE_FIELDS):
        target = fields[index % 2]
        current = result.document.get(field)
        flag = _confidence_flag(result, field)
        if field in missing:
            flag = '<span class="ap-conf-low">· falta</span>'
        label = _FIELD_LABELS[field]
        if flag:
            target.html(
                f'<div style="font-size:12.5px;color:#5A6B85;margin-bottom:-8px;">'
                f'{label} {flag}</div>'
            )
            shown_label = " "
        else:
            shown_label = label
        if field == "document_type":
            options = ["invoice", "proforma_or_advance_request", "other"]
            updates[field] = target.selectbox(
                shown_label, options,
                index=options.index(current) if current in options else 2,
                format_func=lambda value: _DOC_TYPE_LABELS[value],
                key=f"_ap_f_{result.doc_id}_{field}",
                label_visibility="collapsed" if shown_label == " " else "visible",
            )
        else:
            updates[field] = target.text_input(
                shown_label, value="" if current is None else str(current),
                key=f"_ap_f_{result.doc_id}_{field}",
                label_visibility="collapsed" if shown_label == " " else "visible",
            )
    render_pdf_viewer(result)
    return updates


def _render_evidence(active, result, item) -> None:
    st.markdown("##### Evidencia y copiloto")
    controls = []
    if result.engine == "google_document_ai_invoice_parser":
        controls.append(("Extracción", "Google Document AI", "ok"))
    else:
        controls.append(("Extracción", "Motor local controlado", "warn"))
    missing = workflow.missing_critical_fields(result.document)
    controls.append(
        ("Campos críticos", "Completos" if not missing else f"Faltan: {', '.join(missing)}",
         "ok" if not missing else "risk"))
    resolution = getattr(active, "supplier_resolutions", {}).get(str(result.doc_id))
    if resolution:
        estado = resolution.get("status")
        controls.append((
            "Maestro de proveedores",
            {"matched": "Vinculado", "not_found": "No dado de alta",
             "inactive": "Dado de baja", "ambiguous": "Ambiguo"}.get(estado, estado or "—"),
            "ok" if estado == "matched" else "warn"))
    for name, value, tone in controls:
        st.html(
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:6px 0;border-bottom:1px solid #EEF1F5;font-size:13px;">'
            f'<span style="color:#5A6B85;">{name}</span>'
            f'{design.chip(value, tone)}</div>'
        )
    st.write("")
    render_document_agent(active, result, page_key="revision")


def render_workspace() -> None:
    from .pilot_pages_common import active_session_or_resume
    from .pilot_pages_workflow import (
        _confirm_exception,
        _confirm_retention,
    )

    st.html(_STICKY_CSS)
    design.page_header(
        "Revisión",
        "Cola priorizada, documento y copiloto en un solo espacio de trabajo.",
    )
    active = active_session_or_resume("review")
    if active is None:
        return

    queue = workflow.review_queue(
        active.results, active.review_decisions, active.approval_decisions
    )
    if not queue:
        design.empty_state(
            "Sin pendientes de revisión",
            "Ningún documento de la sesión necesita una decisión humana.",
        )
        return

    ordered = []
    for entry in queue:
        priority, _tone = design.priority_tone(entry["reasons"])
        result = entry["result"]
        ordered.append({
            "doc_id": result.doc_id,
            "Prioridad": priority,
            "Documento": result.doc_id,
            "Proveedor": supplier_name(result.document),
            "Estado": ("Pendiente" if entry["pending"]
                       else label_for_code(entry["decision"].get("status") or "Resuelto")),
            "item": entry,
        })
    rank = {"Crítica": 0, "Alta": 1, "Media": 2, "Normal": 3}
    ordered.sort(key=lambda row: (rank.get(row["Prioridad"], 9),
                                  row["Proveedor"].casefold()))

    state_key = _queue_state_key(active)
    idx = min(st.session_state.get(state_key, 0), len(ordered) - 1)
    row = ordered[idx]
    item = row["item"]
    result = item["result"]

    zona_cola, zona_doc, zona_ai = st.columns([1, 2.1, 1.4], gap="medium")
    with zona_cola:
        with st.container(key="ap_review_queue"):
            _render_queue(active, ordered, idx, state_key)
    with zona_doc:
        updates = _render_document(active, result, item)
        _render_action_bar(active, result, item, ordered, idx, state_key,
                           updates, _confirm_retention, _confirm_exception)
    with zona_ai:
        _render_evidence(active, result, item)


def _render_action_bar(active, result, item, ordered, idx, state_key, updates,
                       confirm_retention, confirm_exception) -> None:
    decision = item["decision"]
    if decision:
        st.caption(
            "Última decisión · "
            + label_for_code(decision.get("status"))
            + " · " + str(decision.get("actor") or "—")
            + " · " + format_datetime(decision.get("timestamp"))
        )

    with st.container(key="ap_review_actions"):
        datos = st.columns([1.4, 1])
        actor = datos[0].text_input(
            "Responsable", placeholder="Nombre y apellido",
            key=f"_ap_actor_{result.doc_id}")
        note = datos[1].text_input(
            "Nota o evidencia", key=f"_ap_note_{result.doc_id}",
            help="Obligatoria para retenciones y excepciones.")

        acciones = st.columns([0.8, 1, 1.3, 1.4, 1.7])
        prev_disabled = idx == 0
        if acciones[0].button("Anterior", icon=":material/chevron_left:",
                              disabled=prev_disabled, width="stretch",
                              key=f"_ap_prev_{result.doc_id}"):
            st.session_state[state_key] = max(0, idx - 1)
            st.rerun()
        retain = acciones[1].button(
            "Retener", icon=":material/pause_circle:", width="stretch",
            key=f"_ap_retain_{result.doc_id}")
        is_invoice = result.document.get("document_type") == "invoice"
        exception = acciones[2].button(
            "Registrar excepción", icon=":material/gavel:", width="stretch",
            disabled=is_invoice, key=f"_ap_exc_{result.doc_id}")
        confirm = acciones[3].button(
            "Confirmar datos", type="primary", icon=":material/check_circle:",
            width="stretch", key=f"_ap_confirm_{result.doc_id}")
        confirm_next = acciones[4].button(
            "Confirmar y siguiente", icon=":material/skip_next:", width="stretch",
            key=f"_ap_confirm_next_{result.doc_id}")

    if confirm or confirm_next:
        try:
            sess.confirm_review(active, result.doc_id, actor, updates, note)
        except ValueError as exc:
            st.error(str(exc))
        else:
            sess.persist(active)
            st.toast("Datos confirmados y decisión auditada.", icon=":material/check:")
            if confirm_next and idx + 1 < len(ordered):
                st.session_state[state_key] = idx + 1
            st.rerun()
    elif retain:
        confirm_retention(active, result.doc_id, actor, note)
    elif exception:
        confirm_exception(active, result.doc_id, actor, note)
