"""Navegación persistente, barra superior y acciones globales del producto."""

from __future__ import annotations

import os

import streamlit as st

from ..agent.config import admin_dashboard_enabled
from . import design
from .pilot_format import format_totals, operational_summary, totals_by_currency
from .trial import session as sess


#: Navegación agrupada por intención de uso: primero el trabajo del día, después
#: los datos que lo sostienen y por último la lectura analítica.
PAGE_GROUPS: list[tuple[str, list[dict]]] = [
    ("Trabajo", [
        {"page": "app_pages/inicio.py", "title": "Inicio",
         "icon": ":material/home:", "default": True},
        {"page": "app_pages/ingreso_documentos.py", "title": "Bandeja",
         "icon": ":material/inbox:"},
        {"page": "app_pages/revision_humana.py", "title": "Revisión",
         "icon": ":material/fact_check:", "counter": "pending_review"},
        {"page": "app_pages/propuesta_pago.py", "title": "Pagos",
         "icon": ":material/payments:", "counter": "eligible"},
    ]),
    ("Datos", [
        {"page": "app_pages/documentos.py", "title": "Documentos",
         "icon": ":material/description:"},
        {"page": "app_pages/nuevo_proveedor.py", "title": "Proveedores",
         "icon": ":material/apartment:"},
    ]),
    ("Inteligencia", [
        {"page": "app_pages/indicadores.py", "title": "Indicadores",
         "icon": ":material/analytics:"},
        {"page": "app_pages/auditoria.py", "title": "Auditoría",
         "icon": ":material/history:"},
    ]),
]

#: Compatibilidad: rutas históricas que deben seguir resolviendo.
PAGES = [spec for _, group in PAGE_GROUPS for spec in group]


def _page_specs() -> list[dict]:
    specs = [dict(spec) for spec in PAGES]
    if admin_dashboard_enabled():
        specs.append({
            "page": "app_pages/admin_asistente.py",
            "title": "Operación del asistente",
            "icon": ":material/admin_panel_settings:",
        })
    return specs


def _streamlit_pages() -> list:
    return [
        st.Page(**{k: v for k, v in spec.items() if k != "counter"})
        for spec in _page_specs()
    ]


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
            for key in ("_pilot_preview_seeded", "_ap_agent_conversations",
                        "_ap_pdf_blobs", "_agent_admin_ok", "_auth_ok"):
                st.session_state.pop(key, None)
            st.rerun()


def _seed_local_preview() -> None:
    if os.environ.get("AP_PREVIEW_MODE", "").strip() != "1":
        return
    from .preview_data import seed_preview_session

    seed_preview_session()


_SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] .ap-brand {
  padding: 2px 4px 10px 4px;
}
[data-testid="stSidebar"] .ap-brand-name {
  font-size: 16px; font-weight: 700; letter-spacing: -.01em; color: #FFFFFF;
  line-height: 1.2;
}
[data-testid="stSidebar"] .ap-brand-sub {
  font-size: 11.5px; color: #9DB6DA; margin-top: 2px;
}
[data-testid="stSidebar"] .ap-navgroup {
  font-size: 10.5px; font-weight: 700; letter-spacing: .09em;
  text-transform: uppercase; color: #7E9AC4; margin: 14px 0 2px 6px;
}
</style>
"""


def _sidebar(pages_by_title: dict, summary: dict, active) -> None:
    with st.sidebar:
        st.html(_SIDEBAR_CSS)
        st.html(
            '<div class="ap-brand"><div class="ap-brand-name">AP Control Tower</div>'
            '<div class="ap-brand-sub">Brand UP</div></div>'
        )
        for group_name, specs in PAGE_GROUPS:
            st.html(f'<div class="ap-navgroup">{group_name}</div>')
            for spec in specs:
                page = pages_by_title.get(spec["title"])
                if page is None:
                    continue
                count = summary.get(spec.get("counter") or "", 0)
                label = spec["title"]
                if spec.get("counter") and count:
                    label = f"{spec['title']}  ·  {count}"
                st.page_link(page, label=label, width="stretch")
        admin = pages_by_title.get("Operación del asistente")
        if admin is not None:
            st.html('<div class="ap-navgroup">Administración</div>')
            st.page_link(admin, width="stretch")

        # Metadatos y acciones destructivas al pie, lejos de la navegación.
        st.divider()
        st.caption(
            f"Sesión activa · {len(active.results) + len(active.errors)} documento(s)"
            + (" · historial disponible" if sess.persistence_available()
               else " · solo memoria")
        )
        if active.persistence_error:
            st.warning("El historial no pudo actualizarse; la sesión sigue activa.")
        if st.button(
            "Cerrar sesión de trabajo",
            icon=":material/logout:",
            width="stretch",
            key="_pilot_close_session",
        ):
            _confirm_close_session()


def render() -> None:
    _seed_local_preview()
    design.inject()
    active = sess.get_session()
    summary = operational_summary(active)

    # El widget de navegación se ancla al tope del sidebar y no respeta el orden
    # de escritura: se oculta y los enlaces se dibujan agrupados a mano. Los
    # objetos Page se crean UNA vez: st.page_link solo reconoce los registrados
    # por st.navigation, y recrearlos rompería los enlaces.
    page_objects = _streamlit_pages()
    current = st.navigation(page_objects, position="hidden")
    _sidebar({page.title: page for page in page_objects}, summary, active)
    current.run()
