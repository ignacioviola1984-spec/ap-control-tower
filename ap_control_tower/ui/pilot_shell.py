"""Navegación persistente y acciones globales del producto unificado."""

from __future__ import annotations

import os

import streamlit as st

from ..agent.config import admin_dashboard_enabled
from .pilot_format import format_totals, totals_by_currency
from .trial import session as sess


PAGES = [
    {
        "page": "app_pages/inicio.py",
        "title": "Inicio",
        "icon": ":material/home:",
        "default": True,
    },
    {
        "page": "app_pages/ingreso_documentos.py",
        "title": "Ingreso de documentos",
        "icon": ":material/upload_file:",
    },
    {
        "page": "app_pages/documentos.py",
        "title": "Documentos",
        "icon": ":material/description:",
    },
    {
        "page": "app_pages/revision_humana.py",
        "title": "Revisión humana",
        "icon": ":material/fact_check:",
    },
    {
        "page": "app_pages/propuesta_pago.py",
        "title": "Lote de pago",
        "icon": ":material/payments:",
    },
    {
        "page": "app_pages/auditoria.py",
        "title": "Auditoría",
        "icon": ":material/history:",
    },
    {
        "page": "app_pages/indicadores.py",
        "title": "Indicadores",
        "icon": ":material/analytics:",
    },
    {
        "page": "app_pages/nuevo_proveedor.py",
        "title": "Nuevo proveedor",
        "icon": ":material/person_add:",
    },
]


def _streamlit_pages() -> list:
    """Crea objetos de navegación dentro del contexto de cada ejecución."""
    specs = list(PAGES)
    if admin_dashboard_enabled():
        specs.append(
            {
                "page": "app_pages/admin_asistente.py",
                "title": "Operación del asistente",
                "icon": ":material/admin_panel_settings:",
            }
        )
    return [st.Page(**spec) for spec in specs]


@st.dialog(
    "Cerrar sesión de trabajo",
    width="medium",
    icon=":material/logout:",
    on_dismiss="rerun",
)
def _confirm_close_session() -> None:
    active = sess.get_session()
    count = len(active.results) + len(active.errors)
    totals = format_totals(totals_by_currency(active.results))
    st.write(f"Se cerrará el acceso y se eliminarán de memoria **{count} documento(s)**.")
    if active.results:
        st.write(f"Total documental de la sesión: **{totals}**.")
    if sess.persistence_available():
        st.info("El historial ya guardado en la base permanecerá disponible.")
    else:
        st.warning("Sin historial configurado, los resultados no podrán recuperarse.")
    actor = st.text_input(
        "Responsable",
        placeholder="Nombre y apellido",
        key="_close_session_actor",
    )
    st.caption("La persona indicada quedará registrada en el último evento de la sesión.")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Cancelar", key="_close_session_cancel"):
            st.rerun()
        if st.button(
            "Cerrar sesión",
            type="primary",
            icon=":material/logout:",
            key="_close_session_confirm",
        ):
            if not (actor or "").strip():
                st.error("Ingresá el nombre de la persona responsable para continuar.")
                return
            active.audit.add(
                agent=actor.strip(),
                action="sesion-cerrada",
                result="cerrada-por-usuario",
                evidence={"documentos_en_memoria": count},
            )
            sess.persist(active)
            sess.reset_session()
            st.session_state.pop("_pilot_preview_seeded", None)
            st.session_state.pop("_ap_agent_conversations", None)
            st.session_state.pop("_ap_pdf_blobs", None)
            st.session_state.pop("_agent_admin_ok", None)
            st.session_state.pop("_auth_ok", None)
            st.rerun()


def _seed_local_preview() -> None:
    if os.environ.get("AP_PREVIEW_MODE", "").strip() != "1":
        return
    from .preview_data import seed_preview_session

    seed_preview_session()


def render() -> None:
    _seed_local_preview()
    active = sess.get_session()

    # El widget de navegación se ancla arriba de la barra lateral y no respeta
    # el orden de escritura, así que se oculta y los enlaces se dibujan a mano:
    # es la única forma de dejar la marca por encima de las pestañas.
    pages = _streamlit_pages()
    page = st.navigation(pages, position="hidden")

    with st.sidebar:
        st.markdown("### Torre de Control para Cuentas a Pagar")
        st.markdown("#### Brand UP")
        st.divider()
        for item in pages:
            st.page_link(item, width="stretch")
        st.divider()

    st.sidebar.caption(
        f"Sesión activa · {len(active.results) + len(active.errors)} documento(s)"
        + (" · historial disponible" if sess.persistence_available() else " · solo memoria")
    )
    if active.persistence_error:
        st.sidebar.warning("El historial no pudo actualizarse; la sesión actual sigue activa.")
    if st.sidebar.button(
        "Cerrar sesión de trabajo",
        icon=":material/logout:",
        width="stretch",
        key="_pilot_close_session",
    ):
        _confirm_close_session()

    page.run()
