"""Opcion 1: Probar con mis facturas (carga manual + import Gmail).

Carga manual de uno o varios PDF y (cuando este configurado) importacion desde
Gmail con la etiqueta AP-DEMO. Ambas rutas usan EL MISMO procesamiento y guardan
los resultados SOLO en la sesion (sin cache global ni disco). Los bytes del PDF
se procesan y se descartan; no se conservan.
"""

from __future__ import annotations

import streamlit as st

from ...app import document_ai_configured
from ..components import extraction_view as ev
from ..components import gmail_panel
from . import session as sess


def _process_and_store(files, canal: str) -> None:
    """Procesa [(nombre, bytes)] con progreso y guarda en la sesion."""
    if not files:
        return
    bar = st.progress(0.0, text="Procesando documentos...")

    def _on_progress(index: int, total: int, name: str) -> None:
        bar.progress(index / total, text=f"Procesado {index}/{total}: {name}")

    results, errors = ev.process_files(files, on_progress=_on_progress)
    bar.empty()

    for name, detail in errors:
        st.error(f"No se pudo procesar **{name}**: {detail}")
    if results:
        session = sess.get_session()
        sess.add_results(session, results)
        sess.record_intake(session, canal=canal, cantidad=len(results))
        st.success(f"{len(results)} documento(s) procesado(s) desde {canal}. "
                   "Abrí **Ver resultados con mis facturas**.")


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
    st.markdown("#### Importar desde Gmail (etiqueta AP-DEMO, solo lectura)")
    gmail_panel.render_gmail_panel(
        on_import=lambda files: _process_and_store(files, canal="gmail"))


def render() -> None:
    st.markdown("## Probar con mis facturas")
    if not document_ai_configured():
        st.warning("Document AI no está configurado: los PDF se procesan con el motor "
                   "local y quedan marcados para revisión.")
    st.html(
        "<div class='apct-card'><b>Procesamiento en memoria, solo esta sesión.</b><br>"
        "<span style='color:#5A6572;'>Los documentos se procesan con Google Document AI "
        "cuando está configurado. No se guardan copias: al finalizar la sesión, todo se "
        "borra.</span></div>",
    )
    _render_manual()
    st.markdown("---")
    _render_gmail()
