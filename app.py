"""AP Control Tower: tablero de trabajo diario y PoC documental.

Ejecutar:  streamlit run app.py  (el puerto se pasa por CLI: --server.port)
Requiere la env var AP_DEMO_PASSWORD; sin ella la app no renderiza nada.
El tablero sintetico corre localmente. La vista opcional de documentos reales
usa Google Document AI cuando sus credenciales estan configuradas.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="AP Control Tower",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ap_control_tower.ui.auth import require_password  # noqa: E402

require_password()  # server-side; corta aca si no hay sesion valida

from ap_control_tower.ui import state  # noqa: E402
from ap_control_tower.ui.theme import inject_css, sidebar_brand, sidebar_footer  # noqa: E402
from ap_control_tower.ui.views import (  # noqa: E402
    audit_view,
    business_case,
    exceptions,
    gate,
    inbox,
    invoice_detail,
    pdf_upload,
    review,
)

inject_css()
sidebar_brand()

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
