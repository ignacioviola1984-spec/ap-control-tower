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


#: Portada de acceso. Es la única pantalla con estilo propio: el degradado y el
#: emblema no existen como elementos nativos y son parte de la identidad pedida.
_LOGIN_STYLE = """
<style>
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(1100px 520px at 12% -10%, #DCE9FA 0%, rgba(220,233,250,0) 60%),
    radial-gradient(900px 480px at 105% 108%, #C9DDF7 0%, rgba(201,221,247,0) 62%),
    linear-gradient(160deg, #F7FAFE 0%, #EEF4FC 55%, #E4EEFB 100%);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stImage"] img { filter: drop-shadow(0 6px 14px rgba(15,76,129,.28)); }
/* "Brand UP" en azul, flanqueado por dos filetes. El texto es un elemento
   nativo (accesible y verificable); los filetes son decoración CSS. */
.st-key-ap_login_brand {
  align-items: center; gap: 1rem; margin: .1rem auto 1.6rem; max-width: 430px;
}
.st-key-ap_login_brand::before, .st-key-ap_login_brand::after {
  content: ""; flex: 1; height: 1px; background: #C8D8EC;
}
.st-key-ap_login_brand p {
  color: #1565C0; font-weight: 700; white-space: nowrap; margin: 0;
}
.ap-login-sub {
  text-align: center; color: #4A5B75; font-size: 14.5px; margin: 2px 0 2px 0;
}
.ap-login-value {
  margin: 1.6rem auto 0; max-width: 560px; text-align: center;
  font-size: 13px; color: #5A6B85; line-height: 1.5;
}
.ap-login-foot {
  margin-top: 1.4rem; text-align: center; font-size: .78rem; color: #8394AB;
}
</style>
"""

#: Hexágono con la torre de control en blanco.
_EMBLEM_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="86" height="86" viewBox="0 0 64 64">'
    '<defs><linearGradient id="apg" x1="0.1" y1="0" x2="0.9" y2="1">'
    '<stop offset="0%" stop-color="#4DA3F5"/>'
    '<stop offset="55%" stop-color="#1B6FD4"/>'
    '<stop offset="100%" stop-color="#0A3A80"/>'
    "</linearGradient></defs>"
    '<path d="M32 2.5 57.5 17v30L32 61.5 6.5 47V17Z" fill="url(#apg)"/>'
    # Torre: fuste con remate apuntado y bandas horizontales.
    '<path d="M32 15.5l5.2 5.2v25.8H26.8V20.7Z" fill="#FFFFFF"/>'
    '<rect x="27.6" y="27.5" width="8.8" height="1.9" rx=".95" fill="#1B6FD4" opacity=".55"/>'
    '<rect x="27.6" y="32.4" width="8.8" height="1.9" rx=".95" fill="#1B6FD4" opacity=".55"/>'
    '<rect x="27.6" y="37.3" width="8.8" height="1.9" rx=".95" fill="#1B6FD4" opacity=".55"/>'
    '<rect x="24.4" y="46.5" width="15.2" height="2.6" rx="1.3" fill="#FFFFFF"/>'
    "</svg>"
)


# El emblema se dibuja con st.image: st.html sanitiza tanto <svg> como <img>,
# así que el marcado en línea llegaba vacío al navegador.

# Se reemplazan los tres pilares comerciales (y la afirmación no verificable de
# "los más altos estándares") por una sola línea de valor concreta. El pie es
# texto no interactivo: no hay páginas reales de privacidad/términos todavía.
_VALUE_LINE = """
<div class="ap-login-value">
  Extracción, revisión y propuesta de pago en un circuito auditado, con
  controles deterministas y un copiloto explicable.
</div>
<div class="ap-login-foot">Uso interno · Brand UP</div>
"""


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

    st.html(_LOGIN_STYLE)
    # El encabezado va a ancho completo para que el título entre en una línea;
    # solo la tarjeta de acceso se angosta.
    with st.container(horizontal=True, horizontal_alignment="center"):
        st.image(_EMBLEM_SVG, width=80)
    st.title("AP Control Tower", text_alignment="center")
    st.html('<p class="ap-login-sub">Inteligencia y control para cuentas a pagar</p>')
    with st.container(
        key="ap_login_brand", horizontal=True, horizontal_alignment="center"
    ):
        st.markdown("**Brand UP**")

    left, center, right = st.columns([1, 1.15, 1])
    del left, right
    with center:
        with st.container(border=True):
            st.subheader(
                "Acceso al sistema",
                text_alignment="center",
                divider=False,
            )
            with st.form("system_login", border=False, enter_to_submit=True):
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
    st.html(_VALUE_LINE)
    st.stop()
