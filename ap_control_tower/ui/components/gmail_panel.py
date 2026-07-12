"""Panel de correo reutilizable: listar AP-DEMO e importar PDFs.

Solo lectura: lista mensajes con la etiqueta, muestra remitente/asunto/fecha/
adjuntos y descarga los PDF seleccionados. El QUE hacer con los PDF importados lo
decide el llamador via ``on_import(files)`` (files = [(nombre, bytes)]).
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from ...gmail import build_client, mailbox_configured, mailbox_provider


def _excluded_filenames() -> set[str]:
    raw = os.environ.get("AP_GMAIL_EXCLUDED_FILENAMES", "")
    return {name.strip().casefold() for name in raw.split("|") if name.strip()}


def _visible_attachments(message) -> list:
    excluded = _excluded_filenames()
    return [a for a in message.attachments
            if a.filename.strip().casefold() not in excluded]


def render_gmail_panel(on_import, client=None, *, require_open: bool = False) -> None:
    if require_open and not st.session_state.get("_trial_gmail_browse"):
        st.caption("La carpeta se consulta únicamente cuando lo solicites.")
        if st.button("Consultar correo AP", use_container_width=True,
                     key="_trial_gmail_open"):
            st.session_state["_trial_gmail_browse"] = True
            st.rerun()
        return

    if client is None:
        if not mailbox_configured():
            st.info("El correo AP no está configurado. Podés continuar con la carga "
                    "manual. La conexión al buzón se habilita por secretos de entorno "
                    "y funciona en modo de solo lectura.")
            return
        client = build_client()

    try:
        messages = client.list_messages()
    except Exception as exc:  # credenciales/red: mensaje claro, sin crash
        st.error(f"No se pudo leer el correo AP (solo lectura): {exc}")
        return

    st.caption(f"Conexión: {mailbox_provider() or 'correo'} · solo lectura")

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
    st.info(f"{len(visible_messages)} {mail_label} · {total_pdfs} {pdf_label}. "
            "Todavía no seleccionaste ni procesaste ningún documento.")

    st.dataframe(
        pd.DataFrame([{
            "fecha": m.date,
            "remitente": m.sender,
            "asunto": m.subject,
            "PDF disponibles": len(attachments),
        } for m, attachments in visible_messages]),
        use_container_width=True, hide_index=True,
    )

    options: dict = {}
    for m, attachments in visible_messages:
        for a in attachments:
            options[f"{m.date[:16]} · {m.sender[:28]} · {a.filename}"] = a
    if not options:
        st.caption("Los mensajes con esa etiqueta no traen adjuntos PDF.")
        return

    picked = st.multiselect("**Adjuntos PDF a importar**", list(options))
    if picked and st.button("Importar y procesar seleccionados", type="primary",
                            use_container_width=True):
        files = []
        for label in picked:
            att = options[label]
            data = client.download_attachment(att.message_id, att.attachment_id)
            files.append((att.filename, data))
        on_import(files)
