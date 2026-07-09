"""AP Control Tower: tablero de trabajo diario, en modo demo con datos sinteticos.

Ejecutar:  streamlit run app.py  (el puerto se pasa por CLI: --server.port)
Requiere la env var AP_DEMO_PASSWORD; sin ella la app no renderiza nada.
Corre 100% local: sin API keys, sin integraciones externas, sin red.
"""

from __future__ import annotations

import streamlit as st

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
)

inject_css()
sidebar_brand()

VIEWS = {
    "📥  Corrida del mes": inbox.render,
    "🧾  Detalle de factura": invoice_detail.render,
    "🚨  Cola de excepciones": exceptions.render,
    "✅  Aprobación de pagos (gate)": gate.render,
    "📜  Registro de auditoría": audit_view.render,
    "📊  Caso de negocio": business_case.render,
}

choice = st.sidebar.radio("Navegación", list(VIEWS), label_visibility="collapsed")

run = state.get_run()
sidebar_footer(
    run["result"].run_id if run else None,
    run["result"].commit if run else None,
)

VIEWS[choice]()
