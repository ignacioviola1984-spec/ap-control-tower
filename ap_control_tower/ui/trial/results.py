"""Resultados de la sesión actual del Trial, sin PDF original."""

from __future__ import annotations

import streamlit as st

from ..components import extraction_view as ev
from . import session as sess
from .step_navigation import render_next


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
                "Podés cargar tus facturas desde la primera opción.")

    from .shell import HUMAN_REVIEW

    render_next("Revisión humana", HUMAN_REVIEW,
                key="trial_results_next_review")
