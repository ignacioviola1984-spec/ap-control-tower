"""Navegacion del trial: tres vistas internas y un enlace externo."""

from __future__ import annotations

import streamlit as st

from ..navigation import scroll_to_top_on_change

INTAKE = "🧾  Probar con mis facturas"
RESULTS = "📊  Ver resultados con mis facturas"
BUSINESS_CASE = "📈  Consultar caso de negocio"

# El acceso a la Demo NO es una vista: se renderiza como enlace externo separado.
TRIAL_OPTIONS = [INTAKE, RESULTS, BUSINESS_CASE]


def render() -> None:
    from . import business_case, demo_link, intake, results, session

    session.render_sidebar_actions()

    choice = st.sidebar.radio("Navegación", TRIAL_OPTIONS, label_visibility="collapsed")
    demo_link.render_sidebar()
    if choice == INTAKE:
        intake.render()
    elif choice == RESULTS:
        results.render()
    else:
        business_case.render()
    scroll_to_top_on_change(choice, state_key="_trial_last_view")
