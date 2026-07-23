"""Acceso compartido, validado del lado del servidor.

``AP_SYSTEM_PASSWORD`` es la configuración preferida. ``AP_DEMO_PASSWORD`` se
acepta temporalmente para no interrumpir instalaciones anteriores. Ningún valor
se registra, persiste ni se incluye en mensajes de interfaz.
"""

from __future__ import annotations

import hmac
import os

PRIMARY_PASSWORD_ENV_VAR = "AP_SYSTEM_PASSWORD"
LEGACY_PASSWORD_ENV_VAR = "AP_DEMO_PASSWORD"
# Alias conservado para integraciones internas anteriores.
PASSWORD_ENV_VAR = PRIMARY_PASSWORD_ENV_VAR


def configured_password() -> str | None:
    """Devuelve la contraseña configurada sin revelar cuál variable la aportó."""
    preferred = os.environ.get(PRIMARY_PASSWORD_ENV_VAR)
    if preferred:
        return preferred
    legacy = os.environ.get(LEGACY_PASSWORD_ENV_VAR)
    return legacy if legacy else None


def system_password_configured() -> bool:
    return bool(configured_password())


def demo_password_configured() -> bool:
    """Compatibilidad interna con consumidores anteriores."""
    return system_password_configured()


def verify_password(entered: str, expected: str) -> bool:
    """Comparación en tiempo constante; nunca registra ni persiste valores."""
    if not entered or not expected:
        return False
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def require_password() -> None:
    """Detiene la aplicación hasta autenticar la sesión actual."""
    import streamlit as st

    expected = configured_password()
    if not expected:
        st.title("Torre de Control para Cuentas a Pagar")
        st.markdown("**Brand UP**")
        st.error(
            "El acceso al sistema no está configurado. Contactá al administrador.",
            icon=":material/lock:",
        )
        st.stop()

    if st.session_state.get("_auth_ok"):
        return

    left, center, right = st.columns([1, 1.15, 1])
    del left, right
    with center:
        st.title("Torre de Control para Cuentas a Pagar", text_alignment="center")
        st.markdown("**Brand UP**", text_alignment="center")
        st.subheader("Acceso al Sistema", text_alignment="center")
        with st.form("system_login", border=True, enter_to_submit=True):
            password = st.text_input(
                "Contraseña",
                type="password",
                placeholder="Contraseña",
                autocomplete="current-password",
                icon=":material/lock:",
                key="_system_password_input",
            )
            submitted = st.form_submit_button(
                "Ingresar",
                type="primary",
                icon=":material/login:",
                width="stretch",
            )
        if submitted:
            if verify_password(password or "", expected):
                st.session_state.pop("_system_password_input", None)
                st.session_state["_auth_ok"] = True
                st.rerun()
            st.error("La contraseña ingresada es incorrecta.", icon=":material/error:")
    st.stop()
