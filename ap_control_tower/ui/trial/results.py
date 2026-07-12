"""Resultados actuales e historial persistente del Trial, sin PDF original."""

from __future__ import annotations

import streamlit as st

from ..components import extraction_view as ev
from . import session as sess


def _render_result_set(title: str, results, errors, processing_seconds,
                       proc_seconds, audit, *, persisted: bool,
                       download_key: str) -> None:
    st.markdown(f"#### {title}")
    ev.render_metrics(results, processing_seconds=processing_seconds, errors=errors)
    st.markdown("##### Documentos")
    ev.render_summary_table(results, errors=errors)
    if results:
        ev.render_download(results, key=download_key)
        st.markdown("##### Detalle por documento")
        ev.render_detail(results, audit=audit, proc_seconds=proc_seconds)
    st.markdown("##### Audit trail")
    ev.render_session_audit(audit, persisted=persisted)
    if persisted:
        st.info("Se conservan resultados estructurados, métricas, hash y auditoría. "
                "El PDF original no fue almacenado.")


def _format_run(run: dict) -> str:
    created = run["created_at"]
    stamp = created.astimezone().strftime("%d/%m/%Y %H:%M") if created else "—"
    return (f"{stamp} · {run['documents']} documento(s) · "
            f"{run['errors']} error(es) · {run['run_id']}")


def _render_history(current_run_id: str) -> None:
    st.markdown("### Corridas anteriores")
    st.caption("Consulta histórica independiente: abrir una corrida guardada aquí "
               "no la agrega ni la mezcla con la sesión actual.")
    if not sess.persistence_available():
        st.caption("El historial estará disponible cuando PostgreSQL esté configurado.")
        return
    try:
        runs = sess.saved_runs()
    except Exception as exc:
        st.error(f"No se pudo consultar el historial: {str(exc)[:180]}")
        return
    if not runs:
        st.caption("Todavía no hay corridas guardadas.")
        return

    by_label = {_format_run(run): run for run in runs}
    selected_label = st.selectbox(
        "Seleccionar corrida guardada", list(by_label), key="_trial_history_select")
    selected = by_label[selected_label]
    try:
        stored = sess.load_saved_run(selected["run_id"])
    except Exception as exc:
        st.error(f"No se pudo abrir la corrida: {str(exc)[:180]}")
        return
    if stored is None:
        st.warning("La corrida seleccionada ya no existe.")
        return

    _render_result_set(
        "Resultado guardado", stored.results, stored.errors,
        stored.processing_seconds, stored.proc_seconds, stored.audit,
        persisted=True, download_key=f"trial_download_history_{stored.run_id}")

    confirm = st.checkbox(
        "Confirmo que quiero borrar esta corrida y su audit trail",
        key=f"_trial_delete_confirm_{stored.run_id}")
    if st.button(
        "🗑  Borrar resultados guardados", type="secondary",
        disabled=not confirm, use_container_width=True,
        key=f"_trial_delete_{stored.run_id}",
    ):
        if sess.delete_saved_run(stored.run_id):
            if stored.run_id == current_run_id:
                sess.reset_session()
            st.success("Corrida, resultados y audit trail eliminados.")
            st.rerun()


def render() -> None:
    st.markdown("## Ver resultados con mis facturas")
    session = sess.get_session()
    results = session.results
    errors = session.errors

    if results or errors:
        st.caption("Resultados de la sesión actual. Los PDF se procesan y descartan.")
        _render_result_set(
            "Sesión actual", results, errors, session.processing_seconds,
            session.proc_seconds, session.audit,
            persisted=sess.persistence_available() and not session.persistence_error,
            download_key=f"trial_download_current_{session.audit.run_id}")
        st.markdown("---")
        sess.render_clear_action()
    else:
        st.info("Todavía no procesaste documentos en esta sesión. "
                "Podés cargar una factura o consultar una corrida anterior.")

    st.markdown("---")
    _render_history(session.audit.run_id)
