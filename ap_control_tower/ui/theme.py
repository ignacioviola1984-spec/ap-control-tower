"""Theming corporativo de AP Control Tower: cero look default de Streamlit.

Paleta sobria de software financiero:
  primario   #0F4C81 (azul corporativo)   secundario #12395C / sidebar #0C2438
  semanticos verde #1E8E5A (ok) / rojo #C0392B (bloqueo) / ambar #B7791F (flag)
Tipografia de sistema (sin fuentes externas: la demo corre sin red).
"""

from __future__ import annotations

import streamlit as st

PRIMARY = "#0F4C81"
SIDEBAR_BG = "#0C2438"
OK = "#1E8E5A"
BLOCK = "#C0392B"
FLAG = "#B7791F"
INFO = "#3A6EA5"
MUTED = "#5A6572"
TEXT = "#1A2332"

_CSS = f"""
<style>
/* ---- limpieza del look default ---- */
#MainMenu, footer {{ visibility: hidden; }}
[data-testid="stToolbar"], [data-testid="stStatusWidget"],
[data-testid="stDecoration"] {{ display: none; }}
/* Colapsa la banda superior vacia del header de Streamlit (evita el hueco
   que empujaba el titulo). No ocupa alto y es transparente. */
[data-testid="stHeader"] {{ background: transparent; height: 0; min-height: 0; }}
/* Aire suficiente arriba: el header ya no reserva espacio, asi que el
   padding del contenido debe dar lugar completo al primer titulo. */
.block-container {{ padding-top: 2.6rem; padding-bottom: 3rem; max-width: 1260px; }}
h1, h2 {{ line-height: 1.3; }}
html, body, [class*="css"] {{
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  color: {TEXT};
}}
h1, h2, h3 {{ font-weight: 750; letter-spacing: -0.01em; color: {TEXT}; }}

/* ---- sidebar corporativa ---- */
[data-testid="stSidebar"] {{ background: {SIDEBAR_BG}; }}
[data-testid="stSidebar"] * {{ color: #E8EEF5 !important; }}
[data-testid="stSidebar"] hr {{ border-color: #24466B; }}
[data-testid="stSidebar"] [role="radiogroup"] label {{
  padding: 7px 10px; border-radius: 8px; width: 100%;
}}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {{ background: #16324E; }}
[data-testid="stSidebar"] .stButton > button {{
  background: #163E63; border: 1px solid #2B5D87; color: #FFFFFF !important;
}}
[data-testid="stSidebar"] .stButton > button:hover {{
  background: #1D527F; border-color: #4A7DA6;
}}
[data-testid="stSidebar"] .stButton > button p {{ color: #FFFFFF !important; }}
[data-testid="stSidebar"] .stButton > button:disabled {{
  background: #29445D; color: #D9E4EE !important; opacity: .8;
}}

/* ---- cards y KPIs ---- */
.apct-card {{
  background: #FFFFFF; border: 1px solid #E3E8EF; border-radius: 12px;
  padding: 18px 20px; box-shadow: 0 1px 2px rgba(16,24,40,.04);
  margin-bottom: 12px;
}}
.apct-trial-hero {{
  font-size: clamp(21px, 2vw, 27px); line-height: 1.2; font-weight: 780;
  letter-spacing: -.015em; color: {TEXT}; white-space: nowrap;
  margin: 0 0 16px 0;
}}
.apct-kpi-label {{ font-size: 12px; color: {MUTED}; text-transform: uppercase;
  letter-spacing: .06em; font-weight: 600; }}
.apct-kpi-value {{ font-size: 26px; font-weight: 800; color: {TEXT}; margin-top: 2px; }}
.apct-kpi-sub {{ font-size: 12.5px; color: {MUTED}; margin-top: 2px; }}

/* ---- badges de estado ---- */
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 11.5px; font-weight: 700; letter-spacing: .02em; white-space: nowrap; }}
.b-ok    {{ background: #E7F5EE; color: {OK}; border: 1px solid #BFE3D0; }}
.b-block {{ background: #FBEAE8; color: {BLOCK}; border: 1px solid #F0C4BE; }}
.b-flag  {{ background: #FBF3E4; color: {FLAG}; border: 1px solid #ECD9B4; }}
.b-info  {{ background: #EAF1F9; color: {INFO}; border: 1px solid #C8DAEE; }}
.b-mut   {{ background: #F0F2F5; color: {MUTED}; border: 1px solid #DDE2E9; }}
.b-gate  {{ background: #F1EAFB; color: #6B46C1; border: 1px solid #DBCCF2; }}

/* ---- tabla densa ---- */
.apct-table {{ width: 100%; border-collapse: collapse; font-size: 13px;
  background: #fff; border: 1px solid #E3E8EF; border-radius: 12px; overflow: hidden; }}
.apct-table th {{ text-align: left; background: #F2F5F9; color: {MUTED};
  font-size: 11.5px; text-transform: uppercase; letter-spacing: .05em;
  padding: 9px 12px; border-bottom: 1px solid #E3E8EF; }}
.apct-table td {{ padding: 8px 12px; border-bottom: 1px solid #EEF1F5; vertical-align: middle; }}
.apct-table tr:last-child td {{ border-bottom: none; }}
.apct-table td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}

/* ---- alerta de fraude ---- */
.apct-fraud {{
  background: linear-gradient(135deg, #7B1D12, #A93226); color: #fff;
  border-radius: 12px; padding: 20px 24px; margin-bottom: 14px;
  border: 1px solid #8E2A1F;
}}
.apct-fraud h3 {{ color: #fff; margin: 0 0 6px 0; }}

/* ---- frase guia ---- */
.apct-motto {{ font-size: 12.5px; color: #9FB3C8 !important; font-style: italic; }}

.stButton > button[kind="primary"] {{ background: {PRIMARY}; border: none; }}
</style>
"""


def inject_css() -> None:
    st.html(_CSS)


def badge(text: str, kind: str) -> str:
    return f'<span class="badge b-{kind}">{text}</span>'


STATUS_BADGE = {
    "bloqueada": ("Bloqueada", "block"),
    "en_lote": ("En lote · pendiente gate", "info"),
    "proximo_ciclo": ("Próximo ciclo", "mut"),
    "pendiente_datos_internos": ("Pendiente de datos internos", "flag"),
    "retenido_alta_proveedor": ("Retenida · alta de proveedor", "flag"),
    "anticipo_retenido_sin_aprobacion": ("Anticipo · sin aprobación", "flag"),
    "anticipo_pendiente_factura_final": ("Anticipo · espera factura final", "info"),
    "anticipo_pagado_sin_factura_final": ("Anticipo pagado sin factura", "block"),
    "domiciliacion_pendiente_conciliacion": ("Domiciliación · a conciliar", "info"),
    "tarjeta_pendiente_conciliacion": ("Tarjeta · a conciliar", "info"),
    "otro_documento_revisar": ("Otro documento · revisar", "mut"),
    "lote_devuelto": ("Lote devuelto", "flag"),
    "liberada_al_banco": ("Liberada al banco", "gate"),
    "cerrada": ("Pagada y conciliada", "ok"),
}


def status_badge(status: str) -> str:
    label, kind = STATUS_BADGE.get(status, (status, "mut"))
    return badge(label, kind)


def kpi(label: str, value: str, sub: str = "") -> str:
    return (f'<div class="apct-card"><div class="apct-kpi-label">{label}</div>'
            f'<div class="apct-kpi-value">{value}</div>'
            f'<div class="apct-kpi-sub">{sub}</div></div>')


def eur(amount) -> str:
    """1234567.89 -> '1.234.567,89'"""
    s = f"{amount:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def sidebar_brand(mode: str = "demo") -> None:
    if mode == "trial":
        subtitle = (
            "<div style='font-size:12px;font-weight:700;color:#E8EEF5 !important;"
            "margin-top:4px;'>Prueba de concepto con facturas reales</div>"
            "<div style='font-size:10.5px;line-height:1.35;color:#9FB3C8 !important;"
            "margin-top:3px;'>Extracción, revisión y propuesta de pago en un circuito "
            "completo.</div>")
    else:
        subtitle = (
            "<div style='font-size:12px;color:#9FB3C8 !important;margin-top:2px;'>"
            "Cuentas a Pagar · agentes maker-checker</div>")
    st.sidebar.html(
        "<div style='padding:6px 4px 2px 4px;'>"
        "<div style='font-size:21px;font-weight:800;letter-spacing:-.01em;'>"
        "AP <span style='color:#7FB3E3;'>Control Tower</span></div>"
        + subtitle +
        "</div>",
    )
    st.sidebar.markdown("---")


def sidebar_footer(run_id: str | None, commit: str | None) -> None:
    st.sidebar.markdown("---")
    st.sidebar.html(
        "<div style='padding:0 4px;'>"
        "<div class='apct-motto' style='margin-top:8px;'>"
        "“El sistema se auto-bloquea ante alertas.<br>La aprobación para liberar "
        "dinero es siempre humana.”</div>"
        "<div style='font-size:11px;color:#9FB3C8 !important;margin-top:8px;'>"
        "El humano interviene en dos lugares:<br>"
        "confirma datos en <b>Revisión humana</b> ·<br>"
        "libera dinero en <b>Aprobación de pagos</b></div>"
        "<div style='font-size:11px;color:#9FB3C8 !important;margin-top:8px;'>"
        "Modo demo · corrida AP con datos sintéticos</div></div>",
    )
