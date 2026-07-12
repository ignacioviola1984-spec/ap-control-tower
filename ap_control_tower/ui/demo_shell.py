"""Shell de la app Demo (AP Control Tower).

El tablero sintetico de trabajo diario: corrida del mes, detalle, excepciones,
revision humana, aprobacion (gate), auditoria y caso de negocio. El motor
C0-C11, el maker-checker, el gate y los datos sinteticos NO se tocan. El arranque
comun (page config, password, tema) vive en ``ui.bootstrap``.
"""

from __future__ import annotations

import streamlit as st

from . import state
from .navigation import scroll_to_top_on_change
from .poc_link import render_sidebar as render_poc_link
from .theme import sidebar_footer
from .views import (
    audit_view,
    business_case,
    exceptions,
    gate,
    inbox,
    invoice_detail,
    review,
)

VIEWS = {
    "📥  Corrida del mes": inbox.render,
    "🧾  Detalle de factura": invoice_detail.render,
    "🚨  Cola de excepciones": exceptions.render,
    "📋  Revisión humana": review.render,
    "✅  Aprobación de pagos (gate)": gate.render,
    "📜  Registro de auditoría": audit_view.render,
    "📊  Caso de negocio": business_case.render,
}


def render() -> None:
    choice = st.sidebar.radio("Navegación", list(VIEWS), label_visibility="collapsed")

    run = state.get_run()
    sidebar_footer(
        run["result"].run_id if run else None,
        run["result"].commit if run else None,
    )
    render_poc_link()

    VIEWS[choice]()
    scroll_to_top_on_change(choice, state_key="_demo_last_view")
