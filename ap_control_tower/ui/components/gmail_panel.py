"""Panel de correo reutilizable para consultar e importar PDF.

Solo lectura: lista mensajes con la etiqueta, muestra remitente/asunto/fecha/
adjuntos y descarga los PDF seleccionados. El QUE hacer con los PDF importados lo
decide el llamador via ``on_import(files)`` (files = [(nombre, bytes)]).
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from ...gmail import build_client, mailbox_address, mailbox_configured, mailbox_provider


def _excluded_filenames() -> set[str]:
    raw = os.environ.get("AP_GMAIL_EXCLUDED_FILENAMES", "")
    return {name.strip().casefold() for name in raw.split("|") if name.strip()}


def _visible_attachments(message) -> list:
    excluded = _excluded_filenames()
    return [a for a in message.attachments
            if a.filename.strip().casefold() not in excluded]


@st.cache_data(ttl=120, show_spinner=False)
def _cached_messages(_client, cache_key: str):
    """Lista los correos con PDF. Cachea 2 minutos para que la bandeja se
    muestre sola en cada rerun sin volver a golpear la API de Gmail."""
    del cache_key
    return _client.list_messages()


def render_gmail_panel(on_import, client=None, *, require_open: bool = False) -> None:
    address = mailbox_address()
    st.caption(f"Buzón AP asignado: **{address}**")
    if require_open and not st.session_state.get("_trial_gmail_browse"):
        st.caption("La carpeta se consulta únicamente cuando lo solicites.")
        if st.button("Consultar correo AP", width="stretch",
                     icon=":material/mail:",
                     key="_trial_gmail_open"):
            st.session_state["_trial_gmail_browse"] = True
            st.rerun()
        return

    if client is None:
        if not mailbox_configured():
            st.info(
                f"El buzón AP **{address}** ya está identificado. Para consultar sus "
                "adjuntos falta autorizar la conexión de solo lectura por OAuth o IMAP. "
                "Mientras tanto, podés continuar con la carga manual."
            )
            return
        client = build_client()

    refresh_token = st.session_state.get("_ap_gmail_refresh", 0)
    try:
        messages = _cached_messages(client, f"{address}:{refresh_token}")
    except Exception:  # credenciales/red: mensaje claro, sin crash
        st.error(
            "No se pudo consultar el correo en este momento. Verificá la conexión "
            "o continuá con la carga manual."
        )
        return

    header = st.container(horizontal=True, vertical_alignment="center")
    header.caption(
        f"Conexión: {mailbox_provider() or 'correo'} · {address} · solo lectura"
    )
    if header.button("Actualizar bandeja", icon=":material/refresh:",
                     key="_trial_gmail_refresh"):
        st.session_state["_ap_gmail_refresh"] = refresh_token + 1
        st.rerun()

    if not messages:
        st.caption("No hay mensajes con la etiqueta configurada.")
        return

    visible_messages = [(m, _visible_attachments(m)) for m in messages]
    visible_messages = [(m, attachments) for m, attachments in visible_messages
                        if attachments]
    if not visible_messages:
        st.caption("No hay adjuntos PDF disponibles en la carpeta configurada.")
        return

    total_pdfs = sum(len(attachments) for _, attachments in visible_messages)
    mail_label = "correo encontrado" if len(visible_messages) == 1 else "correos encontrados"
    pdf_label = "PDF disponible" if total_pdfs == 1 else "PDF disponibles"
    st.info(f"{len(visible_messages)} {mail_label} · {total_pdfs} {pdf_label} "
            "en el buzón. Ningún documento se procesa hasta que lo confirmes.",
            icon=":material/mark_email_unread:")

    st.dataframe(
        pd.DataFrame([{
            "fecha": m.date,
            "remitente": m.sender,
            "asunto": m.subject,
            "PDF disponibles": len(attachments),
        } for m, attachments in visible_messages]),
        width="stretch", hide_index=True,
    )

    options: dict = {}
    for m, attachments in visible_messages:
        for a in attachments:
            options[f"{m.date[:16]} · {m.sender[:28]} · {a.filename}"] = a
    if not options:
        st.caption("Los mensajes con esa etiqueta no traen adjuntos PDF.")
        return

    def _importar(labels: list[str]) -> None:
        files = []
        for label in labels:
            att = options[label]
            data = client.download_attachment(att.message_id, att.attachment_id)
            files.append((att.filename, data))
        on_import(files)

    # Los adjuntos llegan preseleccionados: el buzón está dedicado a facturas,
    # así que el caso normal es procesarlos todos y el revisor solo destilda
    # excepciones. Igual nada se procesa sin confirmación explícita.
    picked = st.multiselect(
        "**Adjuntos PDF a procesar**",
        list(options),
        default=list(options),
        placeholder="Seleccionar archivos",
    )
    acciones = st.container(horizontal=True)
    if acciones.button(
            f"Procesar {len(picked)} seleccionado(s)", type="primary",
            icon=":material/play_arrow:", width="stretch",
            disabled=not picked, key="_trial_gmail_process_picked"):
        _importar(picked)
    if len(options) > 1 and acciones.button(
            f"Procesar los {len(options)}", icon=":material/inbox:",
            width="stretch", key="_trial_gmail_process_all"):
        _importar(list(options))
