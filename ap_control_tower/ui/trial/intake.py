"""Opcion 1: Probar con mis facturas (carga manual + correo AP).

Carga manual de uno o varios PDF y (cuando este configurado) importacion desde
Gmail con la etiqueta AP-DEMO. Ambas rutas usan EL MISMO procesamiento y guardan
los resultados SOLO en la sesion (sin cache global ni disco). Los bytes del PDF
se procesan y se descartan; no se conservan.
"""

from __future__ import annotations

import hashlib

import streamlit as st

from ...app import document_ai_configured
from ..components import extraction_view as ev
from ..components import gmail_panel
from . import session as sess


def _process_and_store(files, canal: str) -> None:
    """Procesa [(nombre, bytes)] documento por documento (tiempo individual) y
    guarda TODO en la sesion: exitosos y errores."""
    if not files:
        return
    session = sess.get_session()
    bar = st.progress(0.0, text="Procesando documentos...")
    total = len(files)
    ok = 0
    for index, (name, data) in enumerate(files, 1):
        result, error, seconds = ev.process_one(name, data)
        if error is not None:
            sess.add_error(session, name, error, seconds)
            st.error(f"No se pudo procesar **{name}**: {error}")
        else:
            sess.add_document(
                session, result, seconds,
                file_hash=hashlib.sha256(data).hexdigest(), source=canal)
            ok += 1
        bar.progress(index / total, text=f"Procesado {index}/{total}: {name}")
    bar.empty()

    if ok:
        sess.record_intake(session, canal=canal, cantidad=ok)
        stored = sess.persist(session)
        st.success(f"{ok} documento(s) procesado(s) desde {canal}. "
                   "Abrí **Ver resultados con mis facturas**."
                   + (" Resultado guardado en el historial." if stored else ""))
    elif session.errors:
        sess.persist(session)


def _render_manual() -> None:
    st.markdown("#### Carga manual de PDF")
    uploaded = st.file_uploader(
        "PDFs de factura / OC (uno o varios)", type=["pdf"],
        accept_multiple_files=True, key="_trial_uploader",
    )
    if not uploaded:
        st.caption("Seleccioná uno o más PDF y presioná **Procesar**.")
        return
    if st.button("Procesar PDFs cargados", type="primary", use_container_width=True):
        files = [(f.name, f.getvalue()) for f in uploaded]
        _process_and_store(files, canal="carga-manual")


def _render_gmail() -> None:
    st.markdown("#### Importar desde el correo AP")
    gmail_panel.render_gmail_panel(
        on_import=lambda files: _process_and_store(files, canal="correo-ap"),
        require_open=True)


def render() -> None:
    st.markdown("## Cargá tus facturas reales y verás cómo el agente las procesa en tiempo real")
    if not document_ai_configured():
        st.warning("Document AI no está configurado: los PDF se procesan con el motor "
                   "local y quedan marcados para revisión.")
    st.html(
        "<div class='apct-card'><b>El PDF se usa solo durante el procesamiento.</b><br>"
        "<span style='color:#5A6572;'>Google Document AI extrae los datos y el archivo "
        "original se descarta. Se conservan la extracción, las métricas y la auditoría "
        "hasta que borres la corrida.</span></div>",
    )
    _render_manual()
    st.markdown("---")
    _render_gmail()
