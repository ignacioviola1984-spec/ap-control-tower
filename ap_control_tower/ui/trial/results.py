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
    session = sess.get_session()
    results = session.results
    if not results:
        st.info("Todavía no procesaste documentos en esta sesión. "
                "Andá a **Probar con mis facturas**.")
        return

    ev.render_metrics(results)
    ev.render_summary_table(results)
    ev.render_download(results)

    st.markdown("#### Detalle por documento")
    ev.render_detail(results)

    st.markdown("#### Audit trail de la sesión (temporal)")
    ev.render_session_audit(session.audit)
