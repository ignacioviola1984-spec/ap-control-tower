"""Sistema visual de AP Control Tower: tokens y componentes compartidos.

Un solo lugar define color, espaciado y tipografía. Las páginas componen con
estos bloques en vez de repetir marcado, así la interfaz se mantiene coherente
cuando crece.

Regla de CSS: solo se estilan clases propias (``ap-*``) y contenedores marcados
con ``key=`` (que Streamlit expone como ``.st-key-<key>``, un contrato público).
No se cuelga nada de ``emotion-cache`` ni de estructuras internas, que cambian
entre versiones sin aviso.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

import streamlit as st

# --------------------------------------------------------------- tokens
INK = "#0F1B2D"          # texto principal
INK_SOFT = "#5A6B85"     # texto secundario
CANVAS = "#F4F7FB"       # fondo general, gris azulado muy claro
SURFACE = "#FFFFFF"      # tarjetas y paneles
BORDER = "#DCE3ED"
NAVY = "#0D2149"         # sidebar
BRAND = "#0F4C81"        # acciones primarias
AI = "#6D4AFF"           # RESERVADO a inteligencia artificial
OK = "#127A4B"
WARN = "#9A5B00"
RISK = "#B42318"
INFO = "#0F4C81"

#: Escala de espaciado de 8 px.
SPACE = {"xs": "4px", "sm": "8px", "md": "16px", "lg": "24px", "xl": "32px"}
RADIUS = "12px"
RADIUS_SM = "10px"

_SEMANTIC = {
    "ok": (OK, "#E6F4EC", "#BFE3D0"),
    "warn": (WARN, "#FDF3E2", "#F0DCB4"),
    "risk": (RISK, "#FDECEA", "#F5C6C0"),
    "info": (INFO, "#EAF1F9", "#C8DAEE"),
    "ai": (AI, "#EFEBFF", "#D6CCFF"),
    "muted": (INK_SOFT, "#F1F4F9", "#DEE5EF"),
}

_CSS = f"""
<style>
:root {{
  --ap-ink: {INK}; --ap-ink-soft: {INK_SOFT}; --ap-border: {BORDER};
  --ap-brand: {BRAND}; --ap-ai: {AI}; --ap-radius: {RADIUS};
}}

/* Cifras tabulares: las columnas de importes tienen que alinearse. */
[data-testid="stMetricValue"], .ap-num, .ap-kpi-value, table td, table th {{
  font-variant-numeric: tabular-nums;
}}

/* El valor de métrica de Streamlit viene en ~36px, más grande que el título de
   la página (28px). Con cifras cortas no se nota, pero un importe como
   «EUR 47.412,23» se comía la vista y competía con el encabezado. 24px lo deja
   por debajo del título y por encima del cuerpo, que es la jerarquía real. */
[data-testid="stMetricValue"] {{
  font-size: 24px; font-weight: 650; line-height: 1.3;
}}

/* ---------------------------------------------------------- encabezado */
.ap-page-head {{ margin: 0 0 {SPACE["md"]} 0; }}
.ap-page-head h1 {{
  font-size: 28px; font-weight: 700; letter-spacing: -.02em;
  color: {INK}; margin: 0 0 2px 0; line-height: 1.25;
}}
.ap-page-head p {{ color: {INK_SOFT}; font-size: 14px; margin: 0; }}

/* ---------------------------------------------------------- chips */
.ap-chip {{
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 10px; border-radius: 999px; font-size: 12px;
  font-weight: 600; line-height: 1.7; white-space: nowrap;
}}
.ap-chip .ap-dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
"""
for _name, (_fg, _bg, _bd) in _SEMANTIC.items():
    _CSS += (
        f'.ap-chip.ap-{_name} {{ color: {_fg}; background: {_bg}; '
        f'border: 1px solid {_bd}; }}\n'
    )

_CSS += f"""
/* ---------------------------------------------------------- KPI */
.ap-kpi-label {{
  font-size: 11.5px; font-weight: 600; letter-spacing: .04em;
  text-transform: uppercase; color: {INK_SOFT}; margin: 0;
}}
.ap-kpi-value {{
  font-size: 26px; font-weight: 700; color: {INK}; margin: 2px 0 0 0;
  line-height: 1.15; letter-spacing: -.02em;
}}
.ap-kpi-delta {{ font-size: 12.5px; font-weight: 600; margin-top: 2px; }}
.ap-kpi-delta.up {{ color: {OK}; }}
.ap-kpi-delta.down {{ color: {RISK}; }}
.ap-kpi-delta.flat {{ color: {INK_SOFT}; }}
.ap-kpi-help {{ font-size: 12px; color: {INK_SOFT}; margin-top: 2px; }}

/* ---------------------------------------------------------- briefing */
.ap-brief {{
  background: {SURFACE}; border: 1px solid {BORDER};
  border-left: 3px solid {BRAND}; border-radius: {RADIUS};
  padding: 16px 18px; margin-bottom: {SPACE["md"]};
}}
.ap-brief.ap-brief-ai {{ border-left-color: {AI}; }}
.ap-brief-eyebrow {{
  font-size: 11.5px; font-weight: 700; letter-spacing: .05em;
  text-transform: uppercase; color: {INK_SOFT}; margin: 0 0 6px 0;
  display: flex; align-items: center; gap: 6px;
}}
.ap-brief-text {{ font-size: 15px; line-height: 1.55; color: {INK}; margin: 0; }}
.ap-brief-text b {{ font-weight: 700; }}

/* ---------------------------------------------------------- alertas */
.ap-alert {{
  display: flex; gap: 10px; align-items: flex-start;
  border-radius: {RADIUS_SM}; padding: 12px 14px; margin-bottom: {SPACE["sm"]};
  font-size: 13.5px; line-height: 1.5; border: 1px solid;
}}
.ap-alert-title {{ font-weight: 700; display: block; margin-bottom: 1px; }}
"""
for _name, (_fg, _bg, _bd) in _SEMANTIC.items():
    _CSS += f'.ap-alert.ap-{_name} {{ color: {INK}; background: {_bg}; border-color: {_bd}; }}\n'

_CSS += f"""
/* ---------------------------------------------------------- timeline */
.ap-timeline {{ margin: 0; padding: 0; list-style: none; }}
.ap-timeline li {{
  position: relative; padding: 0 0 18px 22px;
  border-left: 2px solid {BORDER}; margin-left: 5px;
}}
.ap-timeline li:last-child {{ border-left-color: transparent; padding-bottom: 0; }}
.ap-timeline li::before {{
  content: ""; position: absolute; left: -6px; top: 3px;
  width: 10px; height: 10px; border-radius: 50%;
  background: {SURFACE}; border: 2px solid {INK_SOFT};
}}
.ap-timeline li.ap-ok::before {{ border-color: {OK}; }}
.ap-timeline li.ap-risk::before {{ border-color: {RISK}; }}
.ap-timeline li.ap-warn::before {{ border-color: {WARN}; }}
.ap-timeline li.ap-ai::before {{ border-color: {AI}; }}
.ap-tl-when {{ font-size: 11.5px; color: {INK_SOFT}; }}
.ap-tl-what {{ font-size: 13.5px; color: {INK}; font-weight: 600; }}
.ap-tl-who {{ font-size: 12.5px; color: {INK_SOFT}; }}

/* ---------------------------------------------------------- vacíos */
.ap-empty {{
  text-align: center; padding: 28px 20px; color: {INK_SOFT};
  border: 1px dashed {BORDER}; border-radius: {RADIUS}; background: {SURFACE};
}}
.ap-empty h4 {{ color: {INK}; font-size: 15px; margin: 0 0 4px 0; font-weight: 650; }}
.ap-empty p {{ margin: 0; font-size: 13.5px; }}

/* ---------------------------------------------------------- evidencia IA */
.ap-ai-block {{
  border: 1px solid #D6CCFF; background: #FAF8FF; border-radius: {RADIUS_SM};
  padding: 12px 14px; margin-bottom: {SPACE["sm"]};
}}
.ap-ai-head {{
  font-size: 11.5px; font-weight: 700; letter-spacing: .05em; color: {AI};
  text-transform: uppercase; margin-bottom: 4px;
}}

/* ---------------------------------------------------------- skeleton */
@keyframes ap-shimmer {{
  0% {{ background-position: -420px 0; }}
  100% {{ background-position: 420px 0; }}
}}
.ap-skel-line {{
  height: 12px; border-radius: 6px; margin-bottom: 9px;
  background: linear-gradient(90deg, #EDF1F7 25%, #F7F9FC 50%, #EDF1F7 75%);
  background-size: 840px 100%;
  animation: ap-shimmer 1.25s linear infinite;
}}
/* Reserva de layout: el bloque ocupa su alto final desde el primer frame,
   así el contenido que llega después no empuja la página. */
.ap-skel {{ padding: 2px 0; }}
@media (prefers-reduced-motion: reduce) {{
  .ap-skel-line {{ animation: none; }}
}}

/* ---------------------------------------------------------- confianza */
.ap-conf {{
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11.5px; font-weight: 650;
}}
.ap-conf-bar {{
  width: 34px; height: 5px; border-radius: 3px; background: {BORDER};
  overflow: hidden; display: inline-block;
}}
.ap-conf-bar > i {{ display: block; height: 100%; border-radius: 3px; }}

/* ------------------------------------------------- encabezado de entidad */
.ap-entity {{
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px;
  padding-bottom: 8px; margin-bottom: {SPACE["sm"]};
  border-bottom: 1px solid {BORDER};
}}
.ap-entity-name {{
  font-size: 19px; font-weight: 700; letter-spacing: -.01em; color: {INK};
}}
.ap-entity-sub {{ font-size: 13px; color: {INK_SOFT}; }}
.ap-entity-meta {{ margin-left: auto; font-size: 12.5px; color: {INK_SOFT}; }}

/* --------------------------------------------------------- barra fija */
/* Se aplica por `key=` (contrato público `.st-key-*`), nunca por estructura
   interna. El z-index queda por debajo de diálogos y del menú de Streamlit. */
.ap-actionbar-anchor {{ display: none; }}
[class*="st-key-ap_actionbar_"] {{
  position: sticky; bottom: 0; z-index: 20;
  background: {SURFACE}; border: 1px solid {BORDER};
  border-radius: {RADIUS}; padding: 10px 14px; margin-top: {SPACE["sm"]};
  box-shadow: 0 -2px 10px rgba(16,24,40,.07);
}}

/* ------------------------------------------------------ panel actividad */
.ap-act {{ margin: 0; padding: 0; list-style: none; }}
.ap-act li {{
  display: flex; gap: 8px; align-items: baseline;
  padding: 6px 0; border-bottom: 1px solid #EEF1F5; font-size: 13px;
}}
.ap-act li:last-child {{ border-bottom: none; }}
.ap-act-when {{ color: {INK_SOFT}; font-size: 11.5px; white-space: nowrap; }}
.ap-act-what {{ color: {INK}; }}
.ap-act-who {{ margin-left: auto; color: {INK_SOFT}; font-size: 12px; }}

/* Foco de teclado visible en toda la app (accesibilidad). */
:focus-visible {{ outline: 2px solid {BRAND}; outline-offset: 2px; }}

/* Responsive: por debajo de tablet las columnas de Streamlit ya se apilan;
   acá sólo se recupera el espacio horizontal que la barra fija necesita. */
@media (max-width: 640px) {{
  [class*="st-key-ap_actionbar_"] {{ padding: 8px 10px; }}
  .ap-entity-meta {{ margin-left: 0; width: 100%; }}
  .ap-page-head h1 {{ font-size: 23px; }}
}}
</style>
"""


def inject() -> None:
    """Inyecta el sistema visual una vez por ejecución."""
    st.html(_CSS)


# --------------------------------------------------------------- helpers
def _esc(value) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


#: Alias público: todo texto que provenga de un documento se escapa antes de
#: entrar en marcado propio. Nunca se interpola sin pasar por acá.
esc = _esc


Tone = Literal["ok", "warn", "risk", "info", "ai", "muted"]


def page_header(title: str, subtitle: str = "") -> None:
    """Encabezado de página con elementos NATIVOS.

    Se evita a propósito dibujar un <h1> propio con st.html: eso rompe la
    semántica del documento, deja la página sin título accesible y hace que
    las pruebas de interfaz dejen de ver el encabezado.
    """
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def chip(label: str, tone: Tone = "muted", *, dot: bool = True) -> str:
    """Devuelve el marcado del chip para componer dentro de otro bloque."""
    marker = '<span class="ap-dot"></span>' if dot else ""
    return f'<span class="ap-chip ap-{tone}">{marker}{_esc(label)}</span>'


def show_chips(chips: Iterable[str]) -> None:
    body = " ".join(chips)
    if body.strip():
        st.html(f'<div style="display:flex;gap:6px;flex-wrap:wrap;">{body}</div>')


def kpi(label: str, value, *, delta: str | None = None,
        delta_color: Literal["normal", "inverse", "off"] = "normal",
        help_text: str = "", series: list | None = None,
        border: bool = True) -> None:
    """Tarjeta KPI sobre ``st.metric``.

    Se usa el elemento nativo en vez de marcado propio: ya resuelve delta,
    sparkline (``chart_data``), tooltip y borde, y mantiene la métrica
    accesible y visible para las pruebas de interfaz.
    """
    st.metric(
        label,
        value,
        delta=delta,
        delta_color=delta_color,
        help=help_text or None,
        border=border,
        chart_data=series,
        chart_type="line",
    )


def briefing(text_html: str, *, eyebrow: str = "Resumen operativo",
             ai: bool = False) -> None:
    """Briefing determinístico. ``text_html`` ya viene compuesto por el llamador
    (permite <b>), así que solo debe recibir contenido propio, nunca del PDF."""
    css = "ap-brief ap-brief-ai" if ai else "ap-brief"
    st.html(
        f'<div class="{css}"><p class="ap-brief-eyebrow">{_esc(eyebrow)}</p>'
        f'<p class="ap-brief-text">{text_html}</p></div>'
    )


def alert(message: str, *, tone: Tone = "info", title: str = "") -> None:
    head = f'<span class="ap-alert-title">{_esc(title)}</span>' if title else ""
    st.html(f'<div class="ap-alert ap-{tone}">{head}{_esc(message)}</div>')


def empty_state(title: str, detail: str = "") -> None:
    st.html(
        f'<div class="ap-empty"><h4>{_esc(title)}</h4>'
        f'<p>{_esc(detail)}</p></div>'
    )


def timeline(events: list[dict]) -> None:
    """events: [{'when','what','who','tone'}] — tone en ok/warn/risk/ai/muted."""
    if not events:
        empty_state("Sin eventos registrados")
        return
    items = []
    for event in events:
        tone = event.get("tone", "muted")
        who = event.get("who") or ""
        items.append(
            f'<li class="ap-{tone}">'
            f'<div class="ap-tl-when">{_esc(event.get("when"))}</div>'
            f'<div class="ap-tl-what">{_esc(event.get("what"))}</div>'
            + (f'<div class="ap-tl-who">{_esc(who)}</div>' if who else "")
            + "</li>"
        )
    st.html(f'<ul class="ap-timeline">{"".join(items)}</ul>')


def ai_block(title: str, body: str) -> None:
    st.html(
        f'<div class="ap-ai-block"><div class="ap-ai-head">{_esc(title)}</div>'
        f'<div style="font-size:13.5px;line-height:1.5;">{_esc(body)}</div></div>'
    )


# ------------------------------------------------------- estados uniformes
def skeleton(lines: int = 3, *, widths: Iterable[int] | None = None) -> None:
    """Marcador de carga que reserva el alto final del bloque.

    Se dibuja antes de un cálculo caro para que la página no salte cuando el
    contenido llega: el espacio ya está ocupado.
    """
    sizes = list(widths) if widths else [100, 82, 64, 90, 72][:max(1, lines)]
    while len(sizes) < max(1, lines):
        sizes.append(78)
    bars = "".join(
        f'<div class="ap-skel-line" style="width:{int(width)}%;"></div>'
        for width in sizes[:max(1, lines)]
    )
    st.html(f'<div class="ap-skel">{bars}</div>')


def error_state(title: str, detail: str = "", *, retry_label: str = "",
                key: str = "") -> bool:
    """Estado de error uniforme. Devuelve True si se pidió reintentar."""
    alert(detail or "La operación no pudo completarse.", tone="risk", title=title)
    if retry_label:
        return st.button(retry_label, icon=":material/refresh:",
                         key=key or f"_retry_{title}")
    return False


def confidence(value, *, label: str = "confianza") -> str:
    """Indicador de confianza informada por el extractor.

    Devuelve marcado vacío si el extractor no informó confianza: no se inventa
    un número ni se asume "alta" por ausencia de dato.
    """
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return ""
    ratio = max(0.0, min(1.0, ratio))
    color = RISK if ratio < 0.60 else WARN if ratio < 0.80 else OK
    return (
        f'<span class="ap-conf" style="color:{color};" '
        f'title="{_esc(label)} informada por el extractor">'
        f'<span class="ap-conf-bar"><i style="width:{ratio * 100:.0f}%;'
        f'background:{color};"></i></span>{ratio * 100:.0f}%</span>'
    )


def entity_header(name: str, subtitle: str = "", *, chips: Iterable[str] = (),
                  meta: str = "") -> None:
    """Encabezado de una entidad (documento, proveedor, lote)."""
    body = f'<span class="ap-entity-name">{_esc(name)}</span>'
    if subtitle:
        body += f'<span class="ap-entity-sub">{_esc(subtitle)}</span>'
    chips_html = " ".join(chips)
    if chips_html:
        body += f'<span style="display:inline-flex;gap:6px;">{chips_html}</span>'
    if meta:
        body += f'<span class="ap-entity-meta">{_esc(meta)}</span>'
    st.html(f'<div class="ap-entity">{body}</div>')


def action_bar(key: str):
    """Contenedor de acciones fijo al pie del área de trabajo.

    Devuelve un contenedor de Streamlit: las acciones se escriben adentro con
    ``with``. El anclaje es CSS sobre la clave pública ``.st-key-*``.
    """
    return st.container(key=f"ap_actionbar_{key}")


def activity_panel(events: list[dict], *, empty: str = "Sin actividad registrada.") -> None:
    """Actividad reciente: [{'when','what','who'}]."""
    if not events:
        st.caption(empty)
        return
    items = "".join(
        f'<li><span class="ap-act-when">{_esc(item.get("when"))}</span>'
        f'<span class="ap-act-what">{_esc(item.get("what"))}</span>'
        + (f'<span class="ap-act-who">{_esc(item.get("who"))}</span>'
           if item.get("who") else "")
        + "</li>"
        for item in events
    )
    st.html(f'<ul class="ap-act">{items}</ul>')


# --------------------------------------------------------------- formato
def money(amount, currency: str = "EUR") -> str:
    """Formato europeo con separador de miles. Vacío si no es un número."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    formatted = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{currency} {formatted}".strip()


def state_tone(state_code: str) -> Tone:
    """Traduce el estado del documento al tono semántico del sistema visual."""
    return {
        "processed": "ok",
        "approved": "ok",
        "eligible": "info",
        "pending_review": "warn",
        "retained": "warn",
        "rejected": "risk",
        "error": "risk",
        "excluded": "muted",
    }.get(state_code, "muted")


def priority_tone(reasons: list[str]) -> tuple[str, Tone]:
    """Prioridad derivada de los motivos deterministas, no de una heurística.

    El orden refleja consecuencia económica: primero lo que puede hacer perder
    dinero (pagar de más, pagar dos veces, pagar a quien no corresponde).
    """
    text = " ".join(str(item) for item in reasons).casefold()
    if any(key in text for key in (
            "cuenta de cobro", "ya pagada", "no está emitida a nombre",
            "apócrif", "arca")):
        return "Crítica", "risk"
    if any(key in text for key in ("duplicad", "no dado de alta", "dado de baja",
                                   "importe no positivo")):
        return "Alta", "warn"
    if reasons:
        return "Media", "info"
    return "Normal", "muted"
