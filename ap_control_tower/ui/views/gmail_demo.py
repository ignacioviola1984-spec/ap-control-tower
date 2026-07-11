"""Vista Demo: Gmail AP-DEMO (recepcion + extraccion de PDF reales, solo lectura).

Demuestra la recepcion de facturas por Gmail (etiqueta AP-DEMO) y su extraccion
con Document AI, SIN incorporarlas a la corrida sintetica ni persistir nada. Los
resultados viven solo en la sesion de Streamlit. Reemplaza la antigua vista
"PoC documentos reales".
"""

from __future__ import annotations

import streamlit as st

from ..components import extraction_view as ev
from ..components import gmail_panel

_KEY = "_gmail_demo_results"


def render() -> None:
    st.markdown("## Gmail AP-DEMO")
    st.html(
        "<div class='apct-card'><b>Recepción y extracción de facturas por Gmail "
        "(solo lectura).</b><br>"
        "<span style='color:#5A6572;'>Lee mensajes con la etiqueta AP-DEMO, muestra "
        "los adjuntos y los procesa con Document AI. No se suman a la corrida sintética "
        "ni se guardan: es una demostración en memoria.</span></div>",
    )

    if _KEY not in st.session_state:
        st.session_state[_KEY] = []

    def _on_import(files) -> None:
        results, errors = ev.process_files(files)
        for name, detail in errors:
            st.error(f"No se pudo procesar **{name}**: {detail}")
        st.session_state[_KEY] = results

    gmail_panel.render_gmail_panel(on_import=_on_import)

    results = st.session_state.get(_KEY) or []
    if results:
        st.markdown("#### Resultado de la extracción (sesión)")
        ev.render_metrics(results)
        ev.render_summary_table(results)
        ev.render_detail(results)
