"""Password gate server-side de la demo.

La comparacion ocurre en el servidor (Streamlit ejecuta este codigo del lado
del server). El password vive UNICAMENTE en la env var AP_DEMO_PASSWORD:
nunca en el codigo, nunca en el repo. Sin la env var, la app muestra
"demo no configurada" y no renderiza nada. La sesion autenticada dura lo que
dure la session_state.
"""

from __future__ import annotations

import hmac
import os

PASSWORD_ENV_VAR = "AP_DEMO_PASSWORD"


def demo_password_configured() -> bool:
    return bool(os.environ.get(PASSWORD_ENV_VAR))


def verify_password(entered: str, expected: str) -> bool:
    """Comparacion en tiempo constante; nunca loguea ni persiste el valor."""
    if not entered or not expected:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def require_password() -> None:
    """Corta la ejecucion de la app hasta que la sesion este autenticada."""
    import streamlit as st

    expected = os.environ.get(PASSWORD_ENV_VAR)
    if not expected:
        st.markdown(
            "<div style='max-width:520px;margin:12vh auto;text-align:center;"
            "font-family:system-ui;color:#1A2332;'>"
            "<h2 style='margin-bottom:8px;'>Demo no configurada</h2>"
            "<p style='color:#5A6572;'>Falta la variable de entorno "
            "<code>AP_DEMO_PASSWORD</code>. La demo no se renderiza sin ella.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.stop()

    if st.session_state.get("_auth_ok"):
        return

    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            "<div style='text-align:center;margin-top:10vh;margin-bottom:12px;'>"
            "<div style='font-size:30px;font-weight:800;color:#0F4C81;'>AP Control Tower</div>"
            "<div style='color:#5A6572;margin-top:4px;'>Acceso a la demo</div></div>",
            unsafe_allow_html=True,
        )
        with st.form("login", border=True):
            pwd = st.text_input("Password", type="password",
                                label_visibility="collapsed",
                                placeholder="Password de la demo")
            submitted = st.form_submit_button("Entrar", use_container_width=True,
                                              type="primary")
        if submitted:
            if verify_password(pwd, expected):
                st.session_state["_auth_ok"] = True
                st.rerun()
            st.error("Password incorrecta.")
    st.stop()
