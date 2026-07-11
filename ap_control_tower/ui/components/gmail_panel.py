"""Panel Gmail reutilizable (demo y trial): listar AP-DEMO e importar PDFs.

Solo lectura: lista mensajes con la etiqueta, muestra remitente/asunto/fecha/
adjuntos y descarga los PDF seleccionados. El QUE hacer con los PDF importados lo
decide el llamador via ``on_import(files)`` (files = [(nombre, bytes)]).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ...gmail import build_client, gmail_configured


def render_gmail_panel(on_import, client=None) -> None:
    if client is None:
        if not gmail_configured():
            st.info("Gmail no está configurado. Podés continuar con la carga manual. "
                    "Para habilitarlo se configuran por entorno el client id, el client "
                    "secret y el refresh token (solo lectura, etiqueta AP-DEMO).")
            return
        client = build_client()

    try:
        messages = client.list_messages()
    except Exception as exc:  # credenciales/red: mensaje claro, sin crash
        st.error(f"No se pudo leer Gmail (solo lectura): {exc}")
        return

    if not messages:
        st.caption("No hay mensajes con la etiqueta configurada.")
        return

    st.dataframe(
        pd.DataFrame([{
            "fecha": m.date,
            "remitente": m.sender,
            "asunto": m.subject,
            "adjuntos PDF": ", ".join(a.filename for a in m.attachments) or "—",
        } for m in messages]),
        use_container_width=True, hide_index=True,
    )

    options: dict = {}
    for m in messages:
        for a in m.attachments:
            options[f"{m.date[:16]} · {m.sender[:28]} · {a.filename}"] = a
    if not options:
        st.caption("Los mensajes con esa etiqueta no traen adjuntos PDF.")
        return

    picked = st.multiselect("Adjuntos PDF a importar (etiqueta AP-DEMO)", list(options))
    if picked and st.button("Importar y procesar seleccionados", type="primary",
                            use_container_width=True):
        files = []
        for label in picked:
            att = options[label]
            data = client.download_attachment(att.message_id, att.attachment_id)
            files.append((att.filename, data))
        on_import(files)
