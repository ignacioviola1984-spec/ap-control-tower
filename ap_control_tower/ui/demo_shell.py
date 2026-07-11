"""Shell de la app Demo (AP Control Tower).

El tablero sintetico de trabajo diario: corrida del mes, detalle, excepciones,
revision humana, aprobacion (gate), auditoria y caso de negocio. El motor
C0-C11, el maker-checker, el gate y los datos sinteticos NO se tocan. El arranque
comun (page config, password, tema) vive en ``ui.bootstrap``.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from . import state
from .theme import sidebar_footer
from .views import (
    audit_view,
    business_case,
    exceptions,
    gate,
    inbox,
    invoice_detail,
    pdf_upload,
    review,
)

VIEWS = {
    "📥  Corrida del mes": inbox.render,
    "🧾  Detalle de factura": invoice_detail.render,
    "🚨  Cola de excepciones": exceptions.render,
    "📋  Revisión humana": review.render,
    "📄  PoC documentos reales": pdf_upload.render,
    "✅  Aprobación de pagos (gate)": gate.render,
    "📜  Registro de auditoría": audit_view.render,
    "📊  Caso de negocio": business_case.render,
}


def render() -> None:
    choice = st.sidebar.radio("Navegación", list(VIEWS), label_visibility="collapsed")

    # Al cambiar de vista, la pagina arranca al tope. Streamlit conserva el scroll
    # entre reruns, asi que al detectar el cambio se inyecta un script que sube el
    # contenedor principal; el numero de secuencia fuerza su re-ejecucion y el
    # retardo corto gana la carrera contra la restauracion de scroll de Streamlit.
    if st.session_state.get("_last_view") != choice:
        st.session_state["_last_view"] = choice
        st.session_state["_scroll_seq"] = st.session_state.get("_scroll_seq", 0) + 1
        components.html(
            f"""<script>/* vista {st.session_state['_scroll_seq']} */
            const subir = () => {{
              const doc = window.parent.document;
              for (const sel of ['section[data-testid="stMain"]',
                                 '[data-testid="stAppViewContainer"]']) {{
                const el = doc.querySelector(sel);
                if (el) el.scrollTo({{top: 0, left: 0, behavior: "instant"}});
              }}
              window.parent.scrollTo(0, 0);
            }};
            subir(); setTimeout(subir, 120); setTimeout(subir, 350);
            </script>""",
            height=0,
        )

    run = state.get_run()
    sidebar_footer(
        run["result"].run_id if run else None,
        run["result"].commit if run else None,
    )

    VIEWS[choice]()
