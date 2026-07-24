"""Revisión humana y gate separado de propuesta de pago."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from . import design
from .agent_panel import render_document_agent
from .pilot_format import (
    STATE_LABELS,
    decimal_value,
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
    """Workspace de tres zonas (cola · documento · copiloto).

    El fallback nativo tabla→detalle queda en ``_render_human_review_legacy``
    por si el workspace no puede construirse en un entorno dado.
    """
    from .review_workspace import render_workspace

    render_workspace()


def _render_human_review_legacy() -> None:
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
    render_document_agent(active, result, page_key="revision")
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


def _payment_risk_flags(row: dict) -> str:
    """Señales de riesgo del documento en el lote, enunciadas sin datos crudos."""
    return " · ".join(payment_risk_list(row))


def payment_risk_list(row: dict) -> list[str]:
    """Señales de riesgo como lista. Función pura, verificable sin interfaz."""
    text = " ".join(str(item) for item in row["reasons"]).casefold()
    flags = []
    if "cuenta de cobro" in text:
        flags.append("Cambio bancario")
    if "duplicad" in text:
        flags.append("Duplicado")
    if "no dado de alta" in text:
        flags.append("Proveedor nuevo")
    if "ya pagada" in text:
        flags.append("Ya pagada")
    if "excep" in text or row["status"] in {"rejected", "excluded"}:
        flags.append("Excepción")
    return flags


def high_amount_ids(rows: list[dict]) -> set[str]:
    """Documentos de importe alto dentro del propio lote.

    Definición: importe mayor o igual al percentil 90 del lote. Es relativa a
    lo que hay sobre la mesa, no a un umbral fijo inventado, y por eso sigue
    teniendo sentido con lotes de cualquier tamaño.
    """
    montos = []
    for row in rows:
        amount = decimal_value(row["result"].document.get("importe_total"))
        if amount is not None and amount > 0:
            montos.append((amount, str(row["result"].doc_id)))
    if len(montos) < 4:
        return set()
    montos.sort()
    corte = montos[int(len(montos) * 0.9)][0]
    return {doc_id for amount, doc_id in montos if amount >= corte}


def upcoming_due(rows: list[dict], today=None, days: int = 14) -> list[dict]:
    """Vencimientos dentro del horizonte, ordenados por fecha. Función pura."""
    from datetime import date as _date
    from datetime import datetime as _datetime
    from datetime import timedelta as _timedelta

    today = today or _date.today()
    limite = today + _timedelta(days=days)
    salida = []
    for row in rows:
        document = row["result"].document
        texto = str(document.get("fecha_vencimiento_calculada")
                    or document.get("fecha_vencimiento_texto") or "")[:10]
        try:
            vence = _datetime.strptime(texto, "%Y-%m-%d").date()
        except ValueError:
            continue
        if vence <= limite:
            salida.append({
                "doc_id": str(row["result"].doc_id),
                "Proveedor": supplier_name(document),
                "Vence": vence,
                "Importe": format_amount(document.get("importe_total"),
                                         document.get("moneda")),
                "Vencido": vence < today,
            })
    salida.sort(key=lambda item: item["Vence"])
    return salida


def render_payment_proposal() -> None:
    design.page_header(
        "Pagos",
        "Gate humano separado de la confirmación documental. Prepara la "
        "propuesta; no contabiliza ni libera dinero.",
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

    # Encabezado del lote: totales por moneda, riesgos y estado maker-checker.
    criticos = sum(1 for row in rows
                   if design.priority_tone(row["reasons"])[1] == "risk")
    cols = st.columns(4, gap="small")
    with cols[0]:
        design.kpi("Elegibles", len(eligible),
                   help_text="Esperan aprobación de un segundo responsable")
    with cols[1]:
        design.kpi("Aprobados", len(approved), help_text="Pasaron el gate")
    with cols[2]:
        design.kpi("Retenidos / excluidos", len(retained))
    with cols[3]:
        design.kpi("Riesgos críticos", criticos,
                   delta="revisar" if criticos else None,
                   delta_color="inverse" if criticos else "off")

    tot_elegible = format_totals(totals_by_currency(eligible))
    tot_aprobado = format_totals(totals_by_currency(approved))
    resumen = st.container(horizontal=True)
    resumen.html(
        f'<div style="font-size:13.5px;">Total elegible '
        f'<b>{tot_elegible}</b></div>')
    resumen.html(
        f'<div style="font-size:13.5px;">Total aprobado '
        f'<b>{tot_aprobado}</b></div>')
    estado_gate = ("Pendiente de aprobación" if eligible and not approved
                   else "Aprobado para propuesta" if approved
                   else "Sin elegibles")
    resumen.html(design.chip(f"Maker-checker · {estado_gate}",
                             "warn" if eligible and not approved else "ok"))

    _render_maker_checker(active, rows)

    if criticos:
        design.alert(
            f"{criticos} documento(s) elegibles o en lote tienen un riesgo de pago "
            "crítico. Revisá el motivo antes de aprobar.",
            tone="risk", title="Atención",
        )

    _render_upcoming(rows)

    # Los cuatro estados del lote, separados: cada pestaña responde una
    # pregunta distinta y evita que "aprobar" opere sobre filas que no
    # corresponden.
    grupos = [
        ("Elegibles", eligible),
        ("Aprobados", approved),
        ("Retenidos", [r for r in rows if r["status"] == "retained"]),
        ("Excluidos", [r for r in rows
                       if r["status"] in {"rejected", "excluded"}]),
    ]
    pestanas = st.tabs([f"{nombre} · {len(items)}" for nombre, items in grupos])
    altos = high_amount_ids(rows)
    seleccion: list[dict] = []
    for pestana, (nombre, items) in zip(pestanas, grupos):
        with pestana:
            elegidos = _render_group_table(nombre, items, altos)
            if nombre == "Elegibles":
                seleccion = elegidos

    selected_ids = [item["doc_id"] for item in seleccion]

    st.subheader("Datos de la decisión")
    st.caption(
        "La aprobación exige un responsable distinto de quien confirmó los "
        "datos. El sistema lo verifica al confirmar."
    )
    datos = st.columns([1.2, 2, 1.6])
    actor = datos[0].text_input(
        "Responsable de la decisión", placeholder="Nombre y apellido",
        key=f"_pay_actor_{active.audit.run_id}")
    note = datos[1].text_input(
        "Motivo o comentario", key=f"_pay_note_{active.audit.run_id}",
        help="Obligatorio para excluir o rechazar.")
    acknowledgement = datos[2].checkbox(
        "La decisión no libera dinero ni reemplaza la autorización bancaria",
        key=f"_pay_ack_{active.audit.run_id}")

    status = _render_selection_bar(seleccion, altos)
    if status:
        if not selected_ids:
            st.error("Seleccioná al menos un documento elegible.")
        elif not actor.strip():
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
        _render_pending_vendors(active)

    _render_batch_timeline(active, eligible, approved)


_SELECTION_NONCE = "_ap_pay_selection_nonce"


def _render_maker_checker(active, rows) -> None:
    """Quién revisó y quién aprobó, con nombres reales de la auditoría."""
    revisores = sorted({
        str((active.review_decisions.get(str(row["result"].doc_id)) or {}).get("actor") or "")
        for row in rows
    } - {""})
    aprobadores = sorted({
        str((active.approval_decisions.get(str(row["result"].doc_id)) or {}).get("actor") or "")
        for row in rows
    } - {""})
    columnas = st.columns(2, gap="medium")
    columnas[0].caption(
        "Revisó · " + (", ".join(revisores) if revisores else "todavía nadie"))
    columnas[1].caption(
        "Aprobó · " + (", ".join(aprobadores) if aprobadores else "todavía nadie"))


def _render_upcoming(rows) -> None:
    proximos = upcoming_due(rows)
    if not proximos:
        return
    vencidos = sum(1 for item in proximos if item["Vencido"])
    titulo = f"Próximos vencimientos · {len(proximos)}"
    if vencidos:
        titulo += f" ({vencidos} vencido/s)"
    with st.expander(titulo, icon=":material/event:", expanded=bool(vencidos)):
        st.dataframe(
            pd.DataFrame([
                {"Documento": item["doc_id"], "Proveedor": item["Proveedor"],
                 "Vence": item["Vence"], "Importe": item["Importe"],
                 "Estado": "Vencido" if item["Vencido"] else "Por vencer"}
                for item in proximos
            ]),
            hide_index=True, width="stretch",
            column_config={
                "Vence": st.column_config.DateColumn("Vence", format="DD/MM/YYYY"),
            },
        )


def _render_group_table(nombre: str, items: list[dict], altos: set[str]) -> list[dict]:
    """Tabla de un grupo del lote. Devuelve las filas seleccionadas."""
    if not items:
        design.empty_state(f"Sin documentos {nombre.casefold()}")
        return []
    tabla = []
    for row in items:
        result = row["result"]
        document = result.document
        riesgos = payment_risk_list(row)
        if str(result.doc_id) in altos:
            riesgos.append("Alto importe")
        tabla.append({
            "doc_id": str(result.doc_id),
            "Documento": str(result.doc_id),
            "Proveedor": supplier_name(document),
            "Número": document.get("numero_factura") or "—",
            "Vencimiento": document.get("fecha_vencimiento_calculada")
            or document.get("fecha_vencimiento_texto") or "—",
            "Importe": format_amount(document.get("importe_total"),
                                     document.get("moneda")),
            "Estado": STATE_LABELS.get(row["status"], row["status"]),
            "Riesgos": " · ".join(riesgos) or "—",
            "Motivo": " · ".join(row["reasons"]) or "—",
            "row": row,
            "riesgos": riesgos,
        })
    columnas = ("Documento", "Proveedor", "Número", "Vencimiento", "Importe",
                "Riesgos", "Estado", "Motivo")
    frame = pd.DataFrame([{key: item[key] for key in columnas} for item in tabla])
    nonce = st.session_state.get(_SELECTION_NONCE, 0)
    seleccionable = nombre == "Elegibles"
    event = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=320,
        on_select="rerun" if seleccionable else "ignore",
        selection_mode="multi-row" if seleccionable else None,
        key=f"payment_documents_{nombre}_{nonce}" if seleccionable else None,
        column_config={
            "Documento": st.column_config.TextColumn("Documento", pinned=True),
            "Proveedor": st.column_config.TextColumn("Proveedor", pinned=True),
            "Riesgos": st.column_config.TextColumn(
                "Riesgos",
                help="Cambio bancario · Duplicado · Proveedor nuevo · Ya pagada "
                     "· Excepción · Alto importe (percentil 90 del lote)."),
        },
    )
    if not seleccionable:
        return []
    return [tabla[index] for index in list(event.selection.rows)]


def _render_selection_bar(seleccion: list[dict], altos: set[str]) -> str | None:
    """Barra fija con el resumen de la selección y las tres decisiones.

    Vive en ``st.bottom``: acompaña el scroll de una tabla larga, así que el
    responsable siempre ve cuánto está por aprobar sin volver arriba.
    """
    with st.bottom:
        with st.container(border=True, key="ap_payment_bar"):
            if not seleccion:
                st.caption(
                    "Seleccioná una o más filas elegibles para habilitar una "
                    "decisión. Las demás pestañas son de sólo lectura."
                )
                return None
            totales = format_totals(totals_by_currency(
                [item["row"] for item in seleccion]))
            banderas: list[str] = []
            for item in seleccion:
                banderas.extend(item["riesgos"])
            resumen = st.columns([1.5, 2.4, 0.9, 1.0, 0.9, 0.9])
            resumen[0].html(
                f'<div style="font-size:13.5px;line-height:2.4;">'
                f'<b>{len(seleccion)}</b> seleccionado(s)</div>')
            chips = [
                design.chip(f"{texto} · {banderas.count(texto)}",
                            "risk" if texto in {"Ya pagada", "Cambio bancario"}
                            else "warn")
                for texto in sorted(set(banderas))
            ]
            resumen[1].html(
                f'<div style="font-size:13.5px;line-height:2.2;">Total '
                f'<b>{design.esc(totales)}</b> '
                + " ".join(chips) + "</div>"
            )
            limpiar = resumen[2].button(
                "Limpiar", icon=":material/close:", width="stretch",
                key="_pay_clear")
            aprobar = resumen[3].button(
                "Aprobar", type="primary", icon=":material/verified:",
                width="stretch", key="_pay_approve")
            excluir = resumen[4].button(
                "Excluir", icon=":material/block:", width="stretch",
                key="_pay_exclude")
            rechazar = resumen[5].button(
                "Rechazar", icon=":material/cancel:", width="stretch",
                key="_pay_reject")
    if limpiar:
        # La selección de st.dataframe vive en su clave: cambiarla devuelve un
        # widget nuevo, sin selección, sin tocar estado interno de Streamlit.
        st.session_state[_SELECTION_NONCE] = st.session_state.get(
            _SELECTION_NONCE, 0) + 1
        st.rerun()
    if aprobar:
        return "approved"
    if excluir:
        return "excluded"
    if rechazar:
        return "rejected"
    return None


def _render_batch_timeline(active, eligible, approved) -> None:
    """Línea temporal del lote a partir de la auditoría real de la sesión."""
    # Sólo hitos con evento REAL en la auditoría. Los estados que este producto
    # todavía no ejecuta —exportación efectiva y liberación al banco— no se
    # dibujan como pasos futuros: sugerirían un circuito que no existe.
    marks = {
        "sesion-iniciada": ("Sesión creada", "muted"),
        "ingesta": ("Ingreso de documentos", "muted"),
        "revision-confirmada": ("Datos confirmados en revisión", "ok"),
        "documento-confirmado": ("Datos confirmados en revisión", "ok"),
        "revision-retenida": ("Documento retenido", "warn"),
        "excepcion-pago-autorizada": ("Excepción autorizada", "warn"),
        "propuesta-pago-decidida": ("Decisión sobre la propuesta", "ok"),
        "propuesta-aprobada": ("Aprobación para propuesta", "ok"),
        "propuesta-excluida": ("Documento excluido", "warn"),
        "propuesta-rechazada": ("Documento rechazado", "risk"),
    }
    eventos = []
    for event in active.audit.events:
        label_tone = marks.get(event.action)
        if not label_tone:
            continue
        eventos.append({
            "when": format_datetime(event.ts),
            "what": label_tone[0],
            "who": event.agent,
            "tone": label_tone[1],
        })
    if eligible and not approved:
        eventos.append({"when": "Ahora", "what": "Pendiente de aprobación humana",
                        "who": "", "tone": "warn"})
    if not eventos:
        return
    with st.expander("Línea temporal del lote", icon=":material/timeline:"):
        design.timeline(eventos[-12:])


def _render_pending_vendors(active) -> None:
    """Altas de proveedor que deben entrar a Sage junto con el lote.

    Pagar a un proveedor cuya ficha no existe en el ERP deja el pago sin
    imputar, así que el alta viaja con la propuesta y no por separado.
    """
    pendientes = list(getattr(active, "pending_vendors", []) or [])
    if not pendientes:
        return
    from .vendor_intake import altas_xlsx

    st.subheader("Altas de proveedor para Sage")
    st.caption(
        f"{len(pendientes)} proveedor(es) dados de alta en esta sesión que aún "
        "no existen en el maestro del ERP. Importalos antes de imputar el pago."
    )
    st.dataframe(pendientes, hide_index=True, width="stretch")
    st.download_button(
        "Exportar altas de proveedor",
        data=altas_xlsx(pendientes),
        file_name="torre-control-altas-proveedor.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/download:",
        width="stretch",
        key=f"vendor_altas_export_{active.audit.run_id}",
    )
