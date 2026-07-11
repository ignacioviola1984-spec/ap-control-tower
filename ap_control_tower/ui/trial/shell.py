"""Navegacion de la app trial: EXACTAMENTE tres opciones."""

from __future__ import annotations

import streamlit as st

INTAKE = "🧾  Probar con mis facturas"
RESULTS = "📊  Ver resultados con mis facturas"
DEMO = "↗  Abrir la Demo completa"

# Contrato: la app trial tiene exactamente estas tres opciones (ver test_app_modes).
TRIAL_OPTIONS = [INTAKE, RESULTS, DEMO]


def render() -> None:
    from . import demo_link, intake, results, session

    session.render_sidebar_actions()

    choice = st.sidebar.radio("Navegación", TRIAL_OPTIONS, label_visibility="collapsed")
    if choice == INTAKE:
        intake.render()
    elif choice == RESULTS:
        results.render()
    else:
        demo_link.render()
