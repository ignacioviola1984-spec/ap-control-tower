"""Barra superior compartida por todas las páginas del producto.

Da tres cosas que antes había que buscar página por página: dónde estoy
(breadcrumb), cuán fresco es lo que veo (última actualización) y qué hago
ahora (acción primaria contextual). Suma la búsqueda global y el acceso al
copiloto para que no dependan de estar en una pantalla concreta.
"""

from __future__ import annotations

import streamlit as st

from . import design, launcher
from .pilot_format import format_datetime


#: Grupo al que pertenece cada página, para el breadcrumb.
PAGE_GROUP = {
    "Inicio": "Trabajo",
    "Documentos": "Trabajo",
    "Revisión": "Trabajo",
    "Pagos": "Trabajo",
    "Ingreso de documentos": "Trabajo",
    "Proveedores": "Datos",
    "Indicadores": "Inteligencia",
    "Auditoría": "Inteligencia",
    "Operación del asistente": "Administración",
}

#: Acción primaria por página: (etiqueta, icono, ruta destino).
#: "Ingresar documentos" deja de ser una pestaña y pasa a ser la acción
#: primaria del producto, disponible desde cualquier pantalla.
_INTAKE = ("Ingresar documentos", ":material/upload_file:",
           "app_pages/ingreso_documentos.py")
PRIMARY_ACTION = {
    "Inicio": _INTAKE,
    "Documentos": _INTAKE,
    "Ingreso de documentos": ("Ver documentos", ":material/description:",
                              "app_pages/documentos.py"),
    "Revisión": ("Ir a Pagos", ":material/payments:",
                 "app_pages/propuesta_pago.py"),
    "Pagos": ("Ver revisión", ":material/fact_check:",
              "app_pages/revision_humana.py"),
    "Proveedores": _INTAKE,
    "Indicadores": _INTAKE,
    "Auditoría": _INTAKE,
}

_TOPBAR_CSS = """
<style>
.st-key-ap_topbar {
  border-bottom: 1px solid #DCE3ED; padding-bottom: 8px; margin-bottom: 14px;
}
.ap-crumb { font-size: 12.5px; color: #5A6B85; line-height: 2.1; }
.ap-crumb b { color: #0F1B2D; font-weight: 650; }
.ap-fresh { font-size: 12px; color: #5A6B85; line-height: 2.3; white-space: nowrap; }
@media (max-width: 640px) {
  .ap-fresh { white-space: normal; line-height: 1.4; }
}
</style>
"""


def _last_update(active) -> str:
    if active.audit.events:
        return format_datetime(active.audit.events[-1].ts)
    return format_datetime(active.created_at)


def render(page_title: str, active, *, copilot_available: bool) -> None:
    """Dibuja la barra superior. No decide nada: sólo orienta y navega."""
    st.html(_TOPBAR_CSS)
    with st.container(key="ap_topbar"):
        cols = st.columns([3.1, 2.0, 1.0, 1.0, 1.4], gap="small",
                          vertical_alignment="center")
        grupo = PAGE_GROUP.get(page_title, "Trabajo")
        cols[0].html(
            f'<div class="ap-crumb">{design.esc(grupo)} '
            f'<span style="opacity:.5;">›</span> <b>{design.esc(page_title)}</b></div>'
        )
        cols[1].html(
            f'<div class="ap-fresh">Última actualización · {design.esc(_last_update(active))}</div>'
        )
        launcher.open_button(cols[2])

        if cols[3].button(
            "Copiloto",
            icon=":material/auto_awesome:",
            help=("Copiloto disponible: abre Revisión, donde acompaña al documento."
                  if copilot_available
                  else "El copiloto no está configurado en este entorno."),
            key="_ap_topbar_copilot",
        ):
            st.switch_page("app_pages/revision_humana.py")

        accion = PRIMARY_ACTION.get(page_title)
        if accion and cols[4].button(
            accion[0], type="primary", icon=accion[1], width="stretch",
            key="_ap_topbar_primary",
        ):
            st.switch_page(accion[2])

    launcher.render()


__all__ = ["PAGE_GROUP", "PRIMARY_ACTION", "render"]
