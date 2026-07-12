"""Helpers visuales compartidos por revisión y aprobación del Trial."""

from __future__ import annotations

import streamlit as st

from . import session as sess


def active_session_or_resume(key_prefix: str):
    session = sess.get_session()
    if session.results:
        return session
    st.info("No hay documentos en la sesión actual. Podés procesar facturas nuevas "
            "o continuar una corrida guardada.")
    if not sess.persistence_available():
        return None
    try:
        runs = sess.saved_runs()
    except Exception as exc:
        st.error(f"No se pudo consultar el historial: {str(exc)[:160]}")
        return None
    if not runs:
        return None
    labels = {f"{run['created_at'].strftime('%d/%m/%Y %H:%M')} · "
              f"{run['documents']} documento(s) · {run['run_id']}": run["run_id"]
              for run in runs}
    selected = st.selectbox("Corrida guardada", list(labels),
                            key=f"_{key_prefix}_resume_select")
    if st.button("Continuar esta corrida", use_container_width=True,
                 key=f"_{key_prefix}_resume_button"):
        sess.resume_saved_run(labels[selected])
        st.rerun()
    return None
