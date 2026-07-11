"""Enlace externo a la Demo completa (no es una vista del trial)."""

from __future__ import annotations

from html import escape
import os

import streamlit as st

DEMO_URL_ENV = "AP_DEMO_URL"


def demo_url() -> str | None:
    """URL de la app Demo, por variable de entorno (no hardcodeada)."""
    url = os.environ.get(DEMO_URL_ENV)
    return url.strip() if url and url.strip() else None


def render_sidebar() -> None:
    """Muestra el acceso a la otra aplicacion, separado de la navegacion."""
    st.sidebar.markdown("---")
    st.sidebar.caption("Recorrido operativo con datos sintéticos")
    url = demo_url()
    if url:
        safe_url = escape(url, quote=True)
        st.sidebar.html(
            "<a href='" + safe_url + "' target='_blank' rel='noopener noreferrer' "
            "style='display:block;width:100%;box-sizing:border-box;padding:10px 12px;"
            "border-radius:8px;background:#0F4C81;border:1px solid #2E6FA7;"
            "color:#FFFFFF !important;text-decoration:none !important;text-align:center;"
            "font-weight:700;font-size:14px;'>↗&nbsp; Abrir AP Control Tower Demo</a>"
        )
    else:
        st.sidebar.caption(
            f"Demo no configurada (`{DEMO_URL_ENV}`).")
