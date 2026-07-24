"""Componente CCv2 que gobierna el layout del workspace de revisión.

Reparto de responsabilidades, sin excepciones: **JavaScript maneja presentación
e interacción** (colapsar zonas, arrastrar el separador, cambiar de zona en
móvil, atajos de teclado) y **Python conserva toda la autoridad** sobre
validación, decisión, retención, excepción, maker-checker, persistencia y
auditoría. El componente no conoce ninguna regla financiera: emite intenciones
("el usuario pidió confirmar y seguir") y Python decide si eso puede ocurrir.

El redimensionado es en vivo: CCv2 no monta un iframe, así que el JS puede
ajustar el ancho de las columnas reales mientras se arrastra y recién avisar a
Python al soltar. Si no encuentra las columnas (cambio de estructura interna de
Streamlit), degrada a emitir el valor y que Python lo aplique en el próximo
dibujado: se pierde el arrastre en vivo, nunca la funcionalidad.
"""

from __future__ import annotations

import streamlit as st

from . import ccv2

NAME = "ap_review_layout"

#: Claves de zona (contrato público `.st-key-*`) que el JS puede tocar.
ZONE_QUEUE = "ap_zone_queue"
ZONE_DOC = "ap_zone_doc"
ZONE_COPILOT = "ap_zone_copilot"
ZONES_WRAP = "ap_review_zones"

#: Atajos de teclado, documentados en la interfaz y en la ayuda del componente.
SHORTCUTS = [
    ("Alt + ←", "Documento anterior"),
    ("Alt + →", "Documento siguiente"),
    ("Alt + Entrar", "Confirmar y siguiente"),
    ("Alt + R", "Retener"),
    ("Alt + Q", "Mostrar u ocultar la cola"),
    ("Alt + C", "Mostrar u ocultar el copiloto"),
]

_HTML = """
<div class="apw" role="toolbar" aria-label="Disposición del espacio de revisión">
  <div class="apw-group">
    <button type="button" class="apw-btn" data-act="toggle_queue"
            aria-pressed="false">Cola</button>
    <button type="button" class="apw-btn" data-act="toggle_copilot"
            aria-pressed="false">Copiloto</button>
  </div>

  <div class="apw-split" data-role="split">
    <label class="apw-lbl" for="apw-range">Ancho del documento</label>
    <input id="apw-range" class="apw-range" type="range" min="30" max="75" step="1"
           aria-label="Ancho de la zona del documento, en porcentaje" />
    <output class="apw-out" data-role="out">—</output>
  </div>

  <div class="apw-zones" data-role="zones" role="group" aria-label="Zona visible">
    <button type="button" class="apw-zbtn" data-zone="cola">Cola</button>
    <button type="button" class="apw-zbtn" data-zone="documento">Documento</button>
    <button type="button" class="apw-zbtn" data-zone="copiloto">Copiloto</button>
  </div>

  <details class="apw-help">
    <summary>Atajos</summary>
    <ul data-role="shortcuts"></ul>
  </details>
</div>
"""

_CSS = """
:host { display: block; }
.apw {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 6px 10px; margin-bottom: 6px;
  border: 1px solid var(--st-secondary-background-color, #DCE3ED);
  border-radius: 10px;
  font: 500 12.5px/1.5 var(--st-font, system-ui, sans-serif);
  color: var(--st-text-color, #0F1B2D);
}
.apw-group { display: flex; gap: 6px; }
.apw-btn, .apw-zbtn {
  font: inherit; cursor: pointer; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--st-secondary-background-color, #DCE3ED);
  background: transparent; color: inherit;
}
.apw-btn[aria-pressed="true"] { opacity: .55; text-decoration: line-through; }
.apw-zbtn[aria-current="true"] {
  border-color: var(--st-primary-color, #0F4C81);
  color: var(--st-primary-color, #0F4C81);
}
.apw-btn:focus-visible, .apw-zbtn:focus-visible, .apw-range:focus-visible,
.apw-help summary:focus-visible {
  outline: 2px solid var(--st-primary-color, #0F4C81); outline-offset: 2px;
}
.apw-split { display: flex; align-items: center; gap: 8px; }
.apw-lbl { color: var(--st-text-color, #5A6B85); opacity: .8; }
.apw-range { width: 130px; accent-color: var(--st-primary-color, #0F4C81); }
.apw-out { font-variant-numeric: tabular-nums; min-width: 34px; }
.apw-zones { display: none; gap: 6px; }
.apw-help { margin-left: auto; }
.apw-help summary { cursor: pointer; opacity: .75; }
.apw-help ul { margin: 6px 0 0 0; padding-left: 16px; }
.apw-help li { list-style: disc; }
.apw-help kbd {
  font: inherit; border: 1px solid currentColor; border-radius: 4px;
  padding: 0 4px; opacity: .8;
}
/* En pantallas angostas el reparto de anchos no aplica: se muestra una zona
   por vez y aparece el selector de zona. */
@media (max-width: 900px) {
  .apw-split { display: none; }
  .apw-zones { display: flex; }
  .apw-help { margin-left: 0; }
}
"""

_JS = """
const WIDE_QUERY = "(min-width: 901px)"

function zoneElement(doc, key) {
  return doc.querySelector(`[class*="st-key-${key}"]`)
}

// La columna real es el ancestro de la zona. Se busca por data-testid, que es
// el mismo contrato que ya usa el CSS de la aplicacion. Si Streamlit cambia la
// estructura, devuelve null y el componente degrada sin romperse.
function columnOf(element) {
  return element ? element.closest('[data-testid="stColumn"]') : null
}

function applyWidths(doc, docPercent) {
  if (!window.matchMedia(WIDE_QUERY).matches) return false
  const queue = columnOf(zoneElement(doc, "ap_zone_queue"))
  const main = columnOf(zoneElement(doc, "ap_zone_doc"))
  const copilot = columnOf(zoneElement(doc, "ap_zone_copilot"))
  if (!main) return false
  const visible = [queue, copilot].filter(Boolean)
  const rest = visible.length ? (100 - docPercent) / visible.length : 0
  main.style.flex = `1 1 ${docPercent}%`
  visible.forEach((element) => { element.style.flex = `1 1 ${rest}%` })
  return true
}

export default function (component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component
  const doc = parentElement.ownerDocument || document

  const state = (data && data.layout) || {}
  const docPercent = Number(state.doc_percent) || 52
  const queueOff = Boolean(state.queue_collapsed)
  const copilotOff = Boolean(state.copilot_collapsed)
  const zone = state.zone || "documento"

  const range = parentElement.querySelector("#apw-range")
  const out = parentElement.querySelector('[data-role="out"]')
  const list = parentElement.querySelector('[data-role="shortcuts"]')

  if (range && range.value !== String(docPercent)) range.value = String(docPercent)
  if (out) out.textContent = `${docPercent}%`

  parentElement.querySelectorAll(".apw-btn").forEach((button) => {
    const off = button.dataset.act === "toggle_queue" ? queueOff : copilotOff
    button.setAttribute("aria-pressed", off ? "true" : "false")
  })
  parentElement.querySelectorAll(".apw-zbtn").forEach((button) => {
    button.setAttribute("aria-current", button.dataset.zone === zone ? "true" : "false")
  })

  if (list && !list.childElementCount && Array.isArray(data?.shortcuts)) {
    list.innerHTML = data.shortcuts
      .map((item) => `<li><kbd>${item[0]}</kbd> — ${item[1]}</li>`)
      .join("")
  }

  // Reparto de ancho aplicado en vivo sobre las columnas reales.
  applyWidths(doc, docPercent)

  // Zona visible en pantallas angostas: se marca en el contenedor de zonas y
  // el CSS de la aplicacion decide que se muestra.
  const wrap = zoneElement(doc, "ap_review_zones")
  if (wrap) wrap.setAttribute("data-zone", zone)

  parentElement.querySelectorAll(".apw-btn").forEach((button) => {
    button.onclick = () => setTriggerValue("action", button.dataset.act)
  })
  parentElement.querySelectorAll(".apw-zbtn").forEach((button) => {
    button.onclick = () => {
      if (wrap) wrap.setAttribute("data-zone", button.dataset.zone)
      setStateValue("layout", { ...state, zone: button.dataset.zone })
    }
  })

  if (range) {
    range.oninput = () => {
      const value = Number(range.value)
      if (out) out.textContent = `${value}%`
      applyWidths(doc, value)          // en vivo, sin ida y vuelta a Python
    }
    const commit = () => setStateValue(
      "layout", { ...state, doc_percent: Number(range.value) })
    range.onchange = commit
    range.onpointerup = commit
  }

  const onKeyDown = (event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey) return
    const key = (event.key || "").toLowerCase()
    const map = {
      arrowleft: "prev", arrowright: "next", enter: "confirm_next",
      r: "retain", q: "toggle_queue", c: "toggle_copilot",
    }
    const action = map[key]
    if (!action) return
    event.preventDefault()
    setTriggerValue("action", action)
  }
  doc.addEventListener("keydown", onKeyDown, true)
  return () => doc.removeEventListener("keydown", onKeyDown, true)
}
"""

#: Reparto inicial: el documento manda, la cola y el copiloto acompañan.
DEFAULT_LAYOUT = {
    "doc_percent": 52,
    "queue_collapsed": False,
    "copilot_collapsed": False,
    "zone": "documento",
}

STATE_KEY = "_ap_review_layout"


def current_layout() -> dict:
    """Layout vigente: el que dejó el componente, o el reparto inicial."""
    stored = st.session_state.get(STATE_KEY)
    value = {}
    if isinstance(stored, dict):
        value = stored.get("layout") or {}
    elif stored is not None:
        value = getattr(stored, "layout", None) or {}
    layout = dict(DEFAULT_LAYOUT)
    if isinstance(value, dict):
        layout.update({k: v for k, v in value.items() if k in DEFAULT_LAYOUT})
    try:
        layout["doc_percent"] = max(30, min(75, int(layout["doc_percent"])))
    except (TypeError, ValueError):
        layout["doc_percent"] = DEFAULT_LAYOUT["doc_percent"]
    return layout


def column_ratios(layout: dict) -> list[float]:
    """Proporciones de las columnas visibles, en el orden cola · doc · copiloto."""
    doc = float(layout["doc_percent"])
    visibles = 2 - int(bool(layout["queue_collapsed"])) - int(bool(layout["copilot_collapsed"]))
    resto = (100.0 - doc) / visibles if visibles else 0.0
    ratios = []
    if not layout["queue_collapsed"]:
        ratios.append(resto)
    ratios.append(doc if visibles else 100.0)
    if not layout["copilot_collapsed"]:
        ratios.append(resto)
    return ratios


def toggle(layout: dict, action: str) -> dict:
    """Aplica una acción de disposición. Función pura."""
    updated = dict(layout)
    if action == "toggle_queue":
        updated["queue_collapsed"] = not updated["queue_collapsed"]
    elif action == "toggle_copilot":
        updated["copilot_collapsed"] = not updated["copilot_collapsed"]
    return updated


def render(layout: dict) -> str | None:
    """Monta el componente y devuelve la acción disparada, si hubo alguna.

    Nunca levanta: si el entorno no soporta CCv2, el workspace sigue con sus
    controles nativos y sólo se pierden el arrastre y los atajos.
    """
    try:
        montar = ccv2.component(NAME, html=_HTML, css=_CSS, js=_JS)
        resultado = montar(
            key=STATE_KEY,
            data={"layout": layout, "shortcuts": SHORTCUTS},
            default={"layout": layout},
            on_layout_change=lambda: None,
            on_action_change=lambda: None,
        )
    except Exception:  # noqa: BLE001 - la disposición nunca tumba la revisión
        return None
    return getattr(resultado, "action", None)


__all__ = [
    "DEFAULT_LAYOUT", "NAME", "SHORTCUTS", "STATE_KEY", "ZONES_WRAP",
    "ZONE_COPILOT", "ZONE_DOC", "ZONE_QUEUE", "column_ratios", "current_layout",
    "render", "toggle",
]
