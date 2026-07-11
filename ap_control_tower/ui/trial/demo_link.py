"""Opcion 3: abrir la Demo completa (URL por configuracion externa)."""

from __future__ import annotations

import os

import streamlit as st

DEMO_URL_ENV = "AP_DEMO_URL"


def demo_url() -> str | None:
    """URL de la app Demo, por variable de entorno (no hardcodeada)."""
    url = os.environ.get(DEMO_URL_ENV)
    return url.strip() if url and url.strip() else None


def render() -> None:
    st.markdown("## Abrir la Demo completa")
    st.html(
        "<div class='apct-card'><b>AP Control Tower — Demo con datos sintéticos.</b><br>"
        "<span style='color:#5A6572;'>La corrida del mes completa: controles maker-checker, "
        "cola de excepciones, revisión humana, gate de aprobación, auditoría y caso de negocio."
        "</span></div>",
    )
    url = demo_url()
    if url:
        st.link_button("Abrir AP Control Tower Demo ↗", url,
                       use_container_width=True, type="primary")
    else:
        st.info(f"La URL de la Demo no está configurada (variable de entorno `{DEMO_URL_ENV}`).")
