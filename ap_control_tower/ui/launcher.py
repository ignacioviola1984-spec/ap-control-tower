"""Launcher global (paleta de comandos) con atajo Ctrl/Cmd + K.

El atajo se captura con un componente CCv2 mínimo. A diferencia de la API de
componentes v1, CCv2 **no** monta un iframe: el JS corre en el documento de la
aplicación, así que puede escuchar el teclado de toda la página y devolver el
evento a Python con ``setTriggerValue``. Esa es la razón técnica por la que el
atajo global ahora es viable y antes se documentó como limitación.

El componente sólo transporta la intención de abrir. Toda la búsqueda, la
navegación y cualquier lectura de datos ocurren en Python.
"""

from __future__ import annotations

import streamlit as st

from . import ccv2
from .pilot_format import supplier_name
from .trial import session as sess
from .trial import workflow


#: El componente no dibuja nada: sólo escucha el teclado de la página.
_HOTKEY_NAME = "ap_command_hotkey"
_HOTKEY_HTML = "<span class='ap-hotkey' aria-hidden='true'></span>"
_HOTKEY_CSS = """
.ap-hotkey { display: none; }
"""
_HOTKEY_JS = """
export default function (component) {
  const { parentElement, setTriggerValue } = component

  // Un único listener por instancia montada: se guarda en el elemento para
  // poder retirarlo cuando Streamlit desmonta el componente.
  const onKeyDown = (event) => {
    const combo = (event.ctrlKey || event.metaKey) && !event.altKey
    if (!combo) return
    const key = (event.key || "").toLowerCase()
    if (key !== "k") return
    event.preventDefault()
    event.stopPropagation()
    setTriggerValue("open", Date.now())
  }

  document.addEventListener("keydown", onKeyDown, true)
  return () => document.removeEventListener("keydown", onKeyDown, true)
}
"""

#: Destinos de navegación disponibles en el launcher.
_DESTINATIONS = [
    ("Inicio", "app_pages/inicio.py", ":material/home:"),
    ("Documentos", "app_pages/documentos.py", ":material/description:"),
    ("Revisión", "app_pages/revision_humana.py", ":material/fact_check:"),
    ("Pagos", "app_pages/propuesta_pago.py", ":material/payments:"),
    ("Proveedores", "app_pages/nuevo_proveedor.py", ":material/apartment:"),
    ("Indicadores", "app_pages/indicadores.py", ":material/analytics:"),
    ("Auditoría", "app_pages/auditoria.py", ":material/history:"),
    ("Ingresar documentos", "app_pages/ingreso_documentos.py", ":material/upload_file:"),
]

OPEN_KEY = "_ap_launcher_open"
#: Filtro que una acción del launcher deja preparado para la página destino.
PRESET_KEY = "_ap_docs_preset"


def _matches(needle: str, haystack: str) -> bool:
    return needle in haystack.casefold()


def search_index(active) -> list[dict]:
    """Índice de búsqueda de la sesión. Puro: verificable sin interfaz.

    No incluye datos bancarios ni identificadores fiscales: el launcher navega,
    no expone información sensible en una lista.
    """
    entries: list[dict] = []
    for result in workflow.unique_results(active.results):
        document = result.document
        entries.append({
            "tipo": "documento",
            "titulo": str(result.doc_id),
            "detalle": supplier_name(document),
            "extra": str(document.get("numero_factura") or ""),
            "doc_id": str(result.doc_id),
        })
    vistos: set[str] = set()
    for result in workflow.unique_results(active.results):
        nombre = supplier_name(result.document)
        clave = nombre.casefold()
        if clave in vistos or nombre == "—":
            continue
        vistos.add(clave)
        entries.append({
            "tipo": "proveedor",
            "titulo": nombre,
            "detalle": "Proveedor de la sesión",
            "extra": "",
            "doc_id": "",
        })
    return entries


def filter_index(entries: list[dict], query: str) -> list[dict]:
    """Filtra el índice por texto libre. Función pura."""
    needle = (query or "").strip().casefold()
    if not needle:
        return []
    return [
        entry for entry in entries
        if _matches(needle, entry["titulo"])
        or _matches(needle, entry["detalle"])
        or _matches(needle, entry["extra"])
    ]


@st.dialog("Buscar y navegar", width="large", on_dismiss="rerun")
def _palette() -> None:
    active = sess.get_session()
    st.caption(
        "Escribí para buscar un documento o un proveedor de la sesión, o elegí "
        "un destino. Atajo: Ctrl/Cmd + K."
    )
    query = st.text_input(
        "Buscar", placeholder="Documento, proveedor o número",
        icon=":material/search:", key="_ap_launcher_query",
    )

    hits = filter_index(search_index(active), query)
    if query.strip():
        if not hits:
            st.caption("Sin coincidencias en los documentos de esta sesión.")
        for position, entry in enumerate(hits[:8]):
            icono = (":material/description:" if entry["tipo"] == "documento"
                     else ":material/apartment:")
            etiqueta = f'{entry["titulo"]} · {entry["detalle"]}'
            if st.button(etiqueta, icon=icono, width="stretch",
                         key=f"_ap_launcher_hit_{position}"):
                st.session_state[OPEN_KEY] = False
                if entry["tipo"] == "documento":
                    st.session_state[PRESET_KEY] = {"buscar": entry["titulo"]}
                    st.switch_page("app_pages/documentos.py")
                else:
                    st.session_state[PRESET_KEY] = {"buscar": entry["titulo"]}
                    st.switch_page("app_pages/documentos.py")
        st.divider()

    st.caption("Ir a")
    columnas = st.columns(2, gap="small")
    for position, (titulo, ruta, icono) in enumerate(_DESTINATIONS):
        if columnas[position % 2].button(
            titulo, icon=icono, width="stretch",
            key=f"_ap_launcher_go_{position}",
        ):
            st.session_state[OPEN_KEY] = False
            st.switch_page(ruta)

    st.divider()
    st.caption("Consultas contextuales")
    if st.button("Ver documentos que requieren revisión",
                 icon=":material/rule:", width="stretch",
                 key="_ap_launcher_q_review"):
        st.session_state[OPEN_KEY] = False
        st.session_state[PRESET_KEY] = {"vista": "Para revisar"}
        st.switch_page("app_pages/documentos.py")
    if st.button("Ver vencimientos de los próximos 7 días",
                 icon=":material/event:", width="stretch",
                 key="_ap_launcher_q_due"):
        st.session_state[OPEN_KEY] = False
        st.session_state[PRESET_KEY] = {"vista": "Vence esta semana"}
        st.switch_page("app_pages/documentos.py")
    if st.button("Preguntarle al copiloto sobre un documento",
                 icon=":material/auto_awesome:", width="stretch",
                 key="_ap_launcher_q_ai"):
        st.session_state[OPEN_KEY] = False
        st.switch_page("app_pages/revision_humana.py")


def render() -> None:
    """Monta el escucha de teclado y abre la paleta cuando corresponde.

    Se llama una vez por ejecución desde la barra superior. Si el entorno no
    soporta componentes CCv2, el atajo queda inactivo y el botón «Buscar» de la
    barra superior sigue abriendo la paleta: la función nunca rompe la página.
    """
    try:
        montar = ccv2.component(
            _HOTKEY_NAME, html=_HOTKEY_HTML, css=_HOTKEY_CSS, js=_HOTKEY_JS)
        resultado = montar(key="_ap_hotkey", data={}, on_open_change=lambda: None)
    except Exception:  # noqa: BLE001 - un atajo nunca debe tumbar la aplicación
        resultado = None
    if getattr(resultado, "open", None):
        st.session_state[OPEN_KEY] = True
    if st.session_state.get(OPEN_KEY):
        st.session_state[OPEN_KEY] = False
        _palette()


def open_button(container) -> None:
    """Botón visible equivalente al atajo, para quien no lo conozca o no lo tenga."""
    if container.button(
        "Buscar",
        icon=":material/search:",
        help="Buscar documentos, proveedores y destinos · Ctrl/Cmd + K",
        key="_ap_launcher_open_button",
    ):
        st.session_state[OPEN_KEY] = True
        st.rerun()


def consume_preset() -> dict:
    """Devuelve (y limpia) el filtro preparado por una acción del launcher."""
    preset = st.session_state.pop(PRESET_KEY, None)
    return dict(preset) if isinstance(preset, dict) else {}


def preview_preset() -> dict:
    """Lee el filtro preparado sin consumirlo (para pruebas y depuración)."""
    preset = st.session_state.get(PRESET_KEY)
    return dict(preset) if isinstance(preset, dict) else {}


__all__ = [
    "OPEN_KEY", "PRESET_KEY", "consume_preset", "filter_index", "open_button",
    "preview_preset", "render", "search_index",
]
