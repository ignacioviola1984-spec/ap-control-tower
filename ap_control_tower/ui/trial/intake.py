"""Ingreso operativo de documentos por carga manual o correo AP.

Carga manual de uno o varios PDF y (cuando está configurado) importación desde
la bandeja de Cuentas a Pagar. Ambas rutas usan el mismo procesamiento y guardan
los resultados SOLO en la sesion (sin cache global ni disco). Los bytes del PDF
se procesan y se descartan; no se conservan.
"""

from __future__ import annotations

import hashlib

import streamlit as st

from ...app import SageMasterError, document_ai_configured
from ..components import gmail_panel
from ..extraction_runner import process_one
from . import session as sess


def _friendly_error(detail: str) -> str:
    text = str(detail or "").casefold()
    if "pdf" in text or "página" in text or "page" in text:
        return "El PDF no pudo leerse. Verificá que el archivo no esté dañado o protegido."
    return "El documento no pudo procesarse. Revisá el archivo y volvé a intentarlo."


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
        result, error, seconds = process_one(name, data)
        if error is not None:
            friendly = _friendly_error(error)
            sess.add_error(session, name, friendly, seconds)
            st.error(f"No se pudo procesar **{name}**. {friendly}")
        else:
            # Controles ARCA (C10 padrón / C11 APOC): agrega motivos a
            # result.warnings antes de registrar el documento, y audita cada
            # señal. En off solo corre la validación local de CUIT.
            evaluacion = arca_service.enriquecer_resultado(result, session.audit)
            for advertencia in evaluacion.advertencias_globales:
                if advertencia not in advertencias_globales:
                    advertencias_globales.append(advertencia)
            added = sess.add_document(
                session, result, seconds,
                file_hash=file_hash, source=canal)
            if added:
                # Bytes del PDF SOLO en memoria de sesión, para mostrarlo al
                # revisor humano en el detalle. No se persiste ni se envía a OpenAI.
                blobs = st.session_state.setdefault("_ap_pdf_blobs", {})
                blobs[str(result.doc_id)] = data
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
        st.success(
            f"{ok} documento(s) procesado(s) desde {canal}. "
            "Ya están disponibles en **Documentos**."
            + (" Los resultados quedaron guardados en el historial." if stored else "")
        )
        # page_link navega en el click directo; un st.button dentro de este bloque
        # no re-ejecuta este código en el rerun, por eso antes no navegaba.
        st.page_link(
            "app_pages/documentos.py",
            label="Abrir Documentos",
            icon=":material/description:",
        )
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
        st.markdown("#### Cargar documentos")
        uploaded = st.file_uploader(
            "Documentos PDF", type=["pdf"],
            accept_multiple_files=True, key="_trial_uploader",
            help="Podés seleccionar uno o varios archivos PDF.",
        )
        if not uploaded:
            st.caption("Seleccioná uno o más PDF. Los archivos se procesan solo al confirmar.")
            return
        if st.button(
            "Procesar documentos",
            type="primary",
            icon=":material/play_arrow:",
            width="stretch",
        ):
            files = [(f.name, f.getvalue()) for f in uploaded]
            _process_and_store(files, canal="carga-manual")


def render_sage_master() -> None:
    """Carga del maestro de Sage. Vive en la página de proveedores, no en el
    ingreso de documentos: es mantenimiento de datos maestros, no operación."""
    session = sess.get_session()
    with st.container(border=True):
        st.markdown("#### Maestro de proveedores de Sage")
        st.write(
            "Cargá el export XLSX de **proveedores** para vincular cada documento "
            "por Tax ID o nombre. El archivo se usa en memoria y no se guarda."
        )
        summary = session.supplier_master_summary
        if summary:
            st.success(
                f"Maestro aplicado: {summary.get('active_vendors', 0)} proveedor(es) "
                f"activo(s) · referencia {summary.get('fingerprint', '—')}."
            )
            if session.supplier_master is None:
                st.caption(
                    "El proceso reanudado conserva los matches auditados. Volvé a cargar "
                    "el maestro para conciliar documentos nuevos."
                )
        uploaded = st.file_uploader(
            "Export de proveedores de Sage",
            type=["xlsx"],
            accept_multiple_files=False,
            max_upload_size=20,
            key="_trial_sage_vendor_master",
            help="Debe contener Cód. proveedor y Razón social. Un export de clientes será rechazado.",
        )
        if uploaded is None:
            st.caption(
                "Sin el maestro de proveedores, el sistema no tiene contra qué "
                "reconciliar. Cargá el maestro de proveedores antes de subir los documentos."
            )
            return
        if st.button(
            "Validar y aplicar maestro",
            icon=":material/account_tree:",
            width="stretch",
            key="_trial_apply_sage_vendor_master",
        ):
            try:
                summary = sess.load_sage_vendor_master(
                    session, uploaded.name, uploaded.getvalue())
            except SageMasterError as exc:
                st.error(str(exc), icon=":material/error:")
            except Exception:
                st.error(
                    "No fue posible leer el maestro de Sage. Verificá el export y volvé a intentarlo.",
                    icon=":material/error:",
                )
            else:
                stored = sess.persist(session)
                st.success(
                    f"Maestro validado: {summary['active_vendors']} proveedor(es) "
                    "activo(s). La sesión fue reconciliada y auditada."
                    + (" Los resultados quedaron guardados." if stored else "")
                )


def _render_gmail() -> None:
    with st.container(border=True):
        st.markdown("#### Bandeja de correo")
        # La bandeja se muestra sola: la casilla de facturación reenvía acá y el
        # operador tiene que ver lo que llegó sin apretar nada antes.
        gmail_panel.render_gmail_panel(
            on_import=lambda files: _process_and_store(files, canal="correo-ap"),
            require_open=False)


def render() -> None:
    st.title("Ingreso de documentos")
    st.caption("Cargá PDF o consultá la bandeja configurada para Cuentas a Pagar.")
    if not document_ai_configured():
        st.warning(
            "Google Document AI no está configurado. Los PDF se procesarán con el "
            "motor local controlado y quedarán señalados para revisión.",
            icon=":material/warning:",
        )
    _render_manual()
    _render_gmail()
