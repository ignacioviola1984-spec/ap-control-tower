"""Opcion 2: Ver resultados con mis facturas (SOLO de la sesion actual).

Muestra unicamente lo procesado en esta sesion: documentos, campos, cobertura,
confianza, campos ausentes, advertencias, detalle por documento, CSV descargable
y el audit trail temporal. No afirma "exactitud" sin validacion humana.
"""

from __future__ import annotations

import streamlit as st

from ..components import extraction_view as ev
from . import session as sess


def render() -> None:
    st.markdown("## Ver resultados con mis facturas")
    st.caption("Análisis consolidado de esta sesión. Todo vive en memoria y "
               "desaparece al finalizar la sesión.")
    session = sess.get_session()
    results = session.results
    errors = session.errors
    if not results and not errors:
        st.info("Todavía no procesaste documentos en esta sesión. "
                "Andá a **Probar con mis facturas**.")
        return

    st.markdown("#### Indicadores de la sesión")
    ev.render_metrics(results, processing_seconds=session.processing_seconds, errors=errors)

    st.markdown("#### Documentos de la sesión")
    ev.render_summary_table(results, errors=errors)
    if results:
        ev.render_download(results)

    st.markdown("#### Detalle por documento")
    if results:
        ev.render_detail(results, audit=session.audit, proc_seconds=session.proc_seconds)
    else:
        st.caption("No hay documentos procesados con éxito para detallar.")

    st.markdown("#### Audit trail de la sesión (temporal)")
    ev.render_session_audit(session.audit)

    st.markdown("---")
    sess.render_clear_action()
