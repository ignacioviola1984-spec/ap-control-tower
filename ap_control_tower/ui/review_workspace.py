"""Workspace de revisión humana: cola · documento · copiloto en tres zonas.

La disposición (colapsar zonas, repartir el ancho, cambiar de zona en móvil,
atajos de teclado) la gobierna un componente CCv2 —ver ``review_layout``— y el
contenido lo dibuja Streamlit. Esa división es deliberada: el componente no
conoce ninguna regla financiera, sólo emite intenciones, y **Python sigue siendo
la autoridad** de validación, decisión, retención, excepción, maker-checker,
persistencia y auditoría.

Si el componente no puede montarse, el workspace conserva sus tres zonas y su
barra de acciones nativas: se pierden el arrastre y los atajos, nunca la
capacidad de revisar ni de decidir.

Vinculación campo → posición en el PDF: NO implementada. El extractor no
devuelve hoy coordenadas fiables por campo, y dibujar un resaltado sobre una
coordenada inventada sería peor que no dibujarlo. Queda documentado como mejora
futura, condicionada a que la extracción entregue ``bounding boxes``.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from . import design, review_layout
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

_WORKSPACE_CSS = f"""
<style>
/* Barra de acciones fija al pie del área de trabajo. */
.st-key-ap_review_actions {{
  position: sticky; bottom: 0; z-index: 20;
  background: #FFFFFF; border: 1px solid #DCE3ED; border-radius: 12px;
  padding: 10px 14px; box-shadow: 0 -2px 8px rgba(16,24,40,.06);
  margin-top: 8px;
}}
.st-key-{review_layout.ZONE_QUEUE} [data-testid="stVerticalBlockBorderWrapper"] {{
  margin-bottom: 6px;
}}
.ap-conf-low {{ color: #B42318; font-weight: 600; }}
.ap-conf-mid {{ color: #9A5B00; font-weight: 600; }}

/* Móvil y tablet vertical: una zona por vez. El selector vive en el componente
   de disposición y sólo marca `data-zone` en el contenedor de las zonas. */
@media (max-width: 900px) {{
  [class*="st-key-{review_layout.ZONES_WRAP}"][data-zone="documento"]
    [class*="st-key-{review_layout.ZONE_QUEUE}"],
  [class*="st-key-{review_layout.ZONES_WRAP}"][data-zone="documento"]
    [class*="st-key-{review_layout.ZONE_COPILOT}"],
  [class*="st-key-{review_layout.ZONES_WRAP}"][data-zone="cola"]
    [class*="st-key-{review_layout.ZONE_DOC}"],
  [class*="st-key-{review_layout.ZONES_WRAP}"][data-zone="cola"]
    [class*="st-key-{review_layout.ZONE_COPILOT}"],
  [class*="st-key-{review_layout.ZONES_WRAP}"][data-zone="copiloto"]
    [class*="st-key-{review_layout.ZONE_QUEUE}"],
  [class*="st-key-{review_layout.ZONES_WRAP}"][data-zone="copiloto"]
    [class*="st-key-{review_layout.ZONE_DOC}"] {{
    display: none;
  }}
}}
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


def ordered_queue(active) -> list[dict]:
    """Cola de revisión pendiente, ordenada por consecuencia económica.

    Sólo entra lo que todavía espera una decisión. `review_queue` devuelve
    también lo ya resuelto —y lo sigue haciendo, porque es la política y no se
    toca—, pero mostrarlo acá dejaba la tarjeta en pantalla después de confirmar
    o retener: el revisor no distinguía lo hecho de lo pendiente y el contador
    de la barra lateral, que sí cuenta pendientes, quedaba en desacuerdo con la
    lista. Lo resuelto se consulta en Documentos y en Auditoría.

    Función pura.
    """
    queue = workflow.review_queue(
        active.results, active.review_decisions, active.approval_decisions
    )
    ordered = []
    for entry in queue:
        if not entry["pending"]:
            continue
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
    return ordered


def _render_queue(ordered: list[dict], idx: int, state_key: str) -> None:
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
                f'{design.esc(row["Estado"])}</span>'
            )
            if st.button(
                f'{row["Proveedor"][:26]} · {row["Documento"]}',
                key=f"_ap_q_{state_key}_{position}",
                width="stretch",
                type="primary" if selected else "secondary",
            ):
                st.session_state[state_key] = position
                st.rerun()


def _render_document(result, item) -> dict:
    st.markdown("##### Documento y datos extraídos")
    if item["reasons"]:
        for reason in item["reasons"]:
            _l, tone = design.priority_tone([reason])
            design.alert(reason, tone=tone if tone != "muted" else "info",
                         title="Motivo de derivación")

    missing = set(workflow.missing_critical_fields(result.document))
    updates: dict = {}
    campos, visor = st.columns([1, 1], gap="medium")
    with campos:
        for field in workflow.EDITABLE_FIELDS:
            current = result.document.get(field)
            flag = _confidence_flag(result, field)
            if field in missing:
                flag = '<span class="ap-conf-low">· falta</span>'
            label = _FIELD_LABELS[field]
            if flag:
                st.html(
                    f'<div style="font-size:12.5px;color:#5A6B85;margin-bottom:-8px;">'
                    f'{design.esc(label)} {flag}</div>'
                )
                shown_label = " "
            else:
                shown_label = label
            if field == "document_type":
                options = ["invoice", "proforma_or_advance_request", "other"]
                updates[field] = st.selectbox(
                    shown_label, options,
                    index=options.index(current) if current in options else 2,
                    format_func=lambda value: _DOC_TYPE_LABELS[value],
                    key=f"_ap_f_{result.doc_id}_{field}",
                    label_visibility="collapsed" if shown_label == " " else "visible",
                )
            else:
                updates[field] = st.text_input(
                    shown_label, value="" if current is None else str(current),
                    key=f"_ap_f_{result.doc_id}_{field}",
                    label_visibility="collapsed" if shown_label == " " else "visible",
                )
    with visor:
        # Campos y PDF quedan realmente lado a lado: el revisor compara sin
        # desplegar nada ni perder de vista lo que está corrigiendo.
        render_pdf_viewer(result, expanded=True)
    return updates


def _render_evidence(active, result) -> None:
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
            f'<span style="color:#5A6B85;">{design.esc(name)}</span>'
            f'{design.chip(value, tone)}</div>'
        )
    st.write("")
    render_document_agent(active, result, page_key="revision")


def render_workspace() -> None:
    from .pilot_pages_common import active_session_or_resume

    st.html(_WORKSPACE_CSS)
    design.page_header(
        "Revisión",
        "Cola priorizada, documento y copiloto en un solo espacio de trabajo.",
    )
    active = active_session_or_resume("review")
    if active is None:
        return

    ordered = ordered_queue(active)
    if not ordered:
        design.empty_state(
            "Sin pendientes de revisión",
            "Ningún documento de la sesión necesita una decisión humana.",
        )
        return

    state_key = _queue_state_key(active)
    idx = min(max(0, st.session_state.get(state_key, 0)), len(ordered) - 1)

    # 1) Disposición: el componente devuelve la intención del usuario ANTES de
    #    dibujar las zonas, para que el reparto de esta ejecución ya sea el
    #    definitivo y no haya un salto visual.
    layout = review_layout.current_layout()
    accion = review_layout.render(layout)
    if accion in {"toggle_queue", "toggle_copilot"}:
        layout = review_layout.apply_action(layout, accion)
        accion = None
    elif accion == "prev" and idx > 0:
        st.session_state[state_key] = idx - 1
        st.rerun()
    elif accion == "next" and idx + 1 < len(ordered):
        st.session_state[state_key] = idx + 1
        st.rerun()

    row = ordered[idx]
    item = row["item"]
    result = item["result"]

    # 2) Zonas. El contenedor exterior lleva la clave que el componente marca
    #    con `data-zone` para el layout de una zona por vez en pantallas chicas.
    ratios = review_layout.column_ratios(layout)
    with st.container(key=review_layout.ZONES_WRAP):
        columnas = st.columns(ratios, gap="medium")
        siguiente = 0
        if not layout["queue_collapsed"]:
            with columnas[siguiente]:
                with st.container(key=review_layout.ZONE_QUEUE):
                    _render_queue(ordered, idx, state_key)
            siguiente += 1
        with columnas[siguiente]:
            with st.container(key=review_layout.ZONE_DOC):
                updates = _render_document(result, item)
        siguiente += 1
        if not layout["copilot_collapsed"]:
            with columnas[siguiente]:
                with st.container(key=review_layout.ZONE_COPILOT):
                    _render_evidence(active, result)

    # 3) Barra de acciones fija. Es también el camino nativo: si el componente
    #    no monta, todo sigue accionable desde acá.
    _render_action_bar(active, result, item, ordered, idx, state_key, updates,
                       shortcut_action=accion)


def _render_action_bar(active, result, item, ordered, idx, state_key, updates,
                       *, shortcut_action: str | None = None) -> None:
    from .pilot_pages_workflow import _confirm_exception, _confirm_retention

    decision = item["decision"]
    if decision:
        st.caption(
            "Última decisión · "
            + label_for_code(decision.get("status"))
            + " · " + str(decision.get("actor") or "—")
            + " · " + format_datetime(decision.get("timestamp"))
        )

    with st.container(key="ap_review_actions"):
        st.caption(
            f"Documento {idx + 1} de {len(ordered)} · atajos: "
            + " · ".join(f"{combo} {que.lower()}" for combo, que in
                         review_layout.SHORTCUTS[:3])
        )
        datos = st.columns([1.4, 1])
        actor = datos[0].text_input(
            "Responsable", placeholder="Nombre y apellido",
            key=f"_ap_actor_{result.doc_id}")
        note = datos[1].text_input(
            "Nota o evidencia", key=f"_ap_note_{result.doc_id}",
            help="Obligatoria para retenciones y excepciones.")

        acciones = st.columns([0.8, 0.8, 1, 1.3, 1.4, 1.7])
        prev_disabled = idx == 0
        next_disabled = idx + 1 >= len(ordered)
        if acciones[0].button("Anterior", icon=":material/chevron_left:",
                              disabled=prev_disabled, width="stretch",
                              key=f"_ap_prev_{result.doc_id}"):
            st.session_state[state_key] = max(0, idx - 1)
            st.rerun()
        if acciones[1].button("Siguiente", icon=":material/chevron_right:",
                              disabled=next_disabled, width="stretch",
                              key=f"_ap_next_{result.doc_id}"):
            st.session_state[state_key] = min(len(ordered) - 1, idx + 1)
            st.rerun()
        retain = acciones[2].button(
            "Retener", icon=":material/pause_circle:", width="stretch",
            key=f"_ap_retain_{result.doc_id}")
        is_invoice = result.document.get("document_type") == "invoice"
        exception = acciones[3].button(
            "Registrar excepción", icon=":material/gavel:", width="stretch",
            disabled=is_invoice, key=f"_ap_exc_{result.doc_id}",
            help=("Una factura fiscal no admite excepción de pago: se confirma "
                  "o se retiene." if is_invoice else None))
        confirm = acciones[4].button(
            "Confirmar datos", type="primary", icon=":material/check_circle:",
            width="stretch", key=f"_ap_confirm_{result.doc_id}")
        confirm_next = acciones[5].button(
            "Confirmar y siguiente", icon=":material/skip_next:", width="stretch",
            disabled=next_disabled, key=f"_ap_confirm_next_{result.doc_id}")

    # El atajo de teclado entra por el mismo camino que el botón: una sola
    # implementación de la regla, en Python.
    if shortcut_action == "confirm_next":
        confirm_next = True
    elif shortcut_action == "retain":
        retain = True

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
        _confirm_retention(active, result.doc_id, actor, note)
    elif exception:
        _confirm_exception(active, result.doc_id, actor, note)


__all__ = ["ordered_queue", "render_workspace"]
