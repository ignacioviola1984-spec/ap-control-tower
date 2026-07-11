"""Arranque compartido de las dos apps Streamlit (demo y trial).

Un solo repo/motor, dos puntos de entrada delgados que seleccionan el modo por
la variable ``AP_APP_MODE`` (o llamando ``run("trial")`` directo). El password
(``AP_DEMO_PASSWORD``) y el tema son COMPARTIDOS. La demo conserva su URL y su
comportamiento; la app trial reutiliza extraccion, auth y tema sin duplicar el
motor. ``set_page_config`` se llama PRIMERO (requisito de Streamlit) y todos los
imports pesados ocurren dentro de ``run`` para no adelantar comandos de st.
"""

from __future__ import annotations

import streamlit as st

_PAGE = {
    "demo": ("AP Control Tower", "🏦"),
    "trial": ("AP Control Tower · Prueba con tus facturas", "🧾"),
}


def normalize_mode(mode: str | None) -> str:
    return mode if mode in _PAGE else "demo"


def run(mode: str | None = "demo") -> None:
    mode = normalize_mode(mode)
    title, icon = _PAGE[mode]
    st.set_page_config(page_title=title, page_icon=icon, layout="wide",
                       initial_sidebar_state="expanded")

    from .auth import require_password
    require_password()  # server-side; corta aca si no hay sesion valida

    from .theme import inject_css, sidebar_brand
    inject_css()
    sidebar_brand()

    if mode == "trial":
        from .trial.shell import render as render_trial
        render_trial()
    else:
        from .demo_shell import render as render_demo
        render_demo()
