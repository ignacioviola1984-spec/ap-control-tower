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
from .step_navigation import render_next

HERO_TEXT = "Cargá tus facturas reales y verás cómo el agente las procesa en tiempo real"


def _process_and_store(files, canal: str) -> None:
    """Procesa [(nombre, bytes)] documento por documento (tiempo individual) y
    guarda TODO en la sesion: exitosos y errores."""
    from ...controls.arca import service as arca_service

    if not files:
        return
    session = sess.get_session()
    modo_arca = arca_service.modo_actual()
    if modo_arca == "off":
        # Constancia informativa: los controles ARCA no verifican en off.
        arca_service.registrar_modo_off(session.audit)
    bar = st.progress(0.0, text="Procesando documentos...")
    total = len(files)
    ok = 0
    omitted = 0
    advertencias_globales: list[str] = []
    for index, (name, data) in enumerate(files, 1):
        file_hash = hashlib.sha256(data).hexdigest()
        if file_hash in session.file_hashes.values():
            sess.record_event(session, "documento-repetido-omitido", {
                "canal": canal, "motivo": "hash-ya-presente-en-la-corrida"})
            omitted += 1
            bar.progress(index / total, text=f"Omitido {index}/{total}: {name}")
            continue
        result, error, seconds = ev.process_one(name, data)
        if error is not None:
            sess.add_error(session, name, error, seconds)
            st.error(f"No se pudo procesar **{name}**: {error}")
        else:
            # Controles ARCA (C10 padrón / C11 APOC): agrega motivos a
            # result.warnings ANTES de registrar el documento, y audita cada
            # señal. En off solo corre la validación local de CUIT.
            evaluacion = arca_service.enriquecer_resultado(result, session.audit)
            for advertencia in evaluacion.advertencias_globales:
                if advertencia not in advertencias_globales:
                    advertencias_globales.append(advertencia)
            added = sess.add_document(
                session, result, seconds,
                file_hash=file_hash, source=canal)
            ok += int(added)
            omitted += int(not added)
        bar.progress(index / total, text=f"Procesado {index}/{total}: {name}")
    bar.empty()
    for advertencia in advertencias_globales:
        st.warning(advertencia)
        sess.record_event(session, "advertencia-global-arca",
                          {"motivo": advertencia})

    if ok:
        sess.record_intake(session, canal=canal, cantidad=ok)
        stored = sess.persist(session)
        st.success(f"{ok} documento(s) procesado(s) desde {canal}. "
                   "Abrí **Ver resultados con mis facturas**."
                   + (" Resultado guardado en el historial." if stored else ""))
    elif omitted:
        sess.persist(session)
        st.info(f"{omitted} documento(s) ya estaban procesados en esta sesión; "
                "no se volvieron a enviar a Document AI.")
    elif session.errors:
        sess.persist(session)
    if ok and omitted:
        st.info(f"{omitted} documento(s) repetido(s) fueron omitidos.")


def _render_manual() -> None:
    with st.container(border=True):
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
    with st.container(border=True):
        st.markdown("#### Importar desde el correo AP")
        gmail_panel.render_gmail_panel(
            on_import=lambda files: _process_and_store(files, canal="correo-ap"),
            require_open=True)


def render() -> None:
    st.html(f"<div class='apct-trial-hero'>{HERO_TEXT}</div>")
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
    from .shell import RESULTS

    render_next("Ver resultados con mis facturas", RESULTS,
                key="trial_intake_next_results")
