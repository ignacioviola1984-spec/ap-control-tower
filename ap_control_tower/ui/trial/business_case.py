"""Caso de negocio del trial, calculado solo con Google Document AI.

La vista cruza los resultados estructurados de la sesion con el AS-IS entregado
por la consultora. No afirma precision sin validacion humana y no atribuye al
MVP integraciones que aun no existen.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from ..components import extraction_view as ev
from . import session as sess
from . import workflow

MANAGED_ENGINE = "google_document_ai_invoice_parser"

# Son los campos del AS-IS que el esquema actual puede medir honestamente.
# La leyenda/descripcion figura en el AS-IS, pero aun no es un campo estructurado
# canonico; se informa como brecha y no se inventa en la cobertura.
CRITICAL_FIELDS = (
    "proveedor_nombre_comercial",
    "numero_factura",
    "fecha_emision",
    "importe_total",
    "moneda",
)

FIELD_LABELS = {
    "proveedor_nombre_comercial": "Proveedor",
    "numero_factura": "Número de factura",
    "fecha_emision": "Fecha de emisión",
    "importe_total": "Importe total (impuestos incluidos)",
    "moneda": "Moneda",
}


def _present(value) -> bool:
    return value not in (None, "", [], {})


def managed_results(results) -> list:
    """Resultados admisibles como evidencia del parser administrado."""
    return [r for r in results if r.engine == MANAGED_ENGINE]


@dataclass(frozen=True)
class BusinessMetrics:
    documents: int
    invoices: int
    critical_found: int
    critical_possible: int
    coverage: float
    confidence: float | None
    clean_documents: int
    review_documents: int
    po_documents: int
    non_po_documents: int
    total_seconds: float
    average_seconds: float


def calculate_metrics(results, proc_seconds: dict | None = None) -> BusinessMetrics:
    """Calcula evidencia descriptiva, nunca exactitud validada."""
    eligible = managed_results(results)
    invoices = [r for r in eligible if r.document.get("document_type") == "invoice"]
    found = sum(
        1 for r in invoices for field in CRITICAL_FIELDS
        if _present(r.document.get(field))
    )
    possible = len(invoices) * len(CRITICAL_FIELDS)
    confidences = [
        float(r.field_confidences[field])
        for r in invoices
        for field in CRITICAL_FIELDS
        if field in r.field_confidences and _present(r.document.get(field))
    ]
    times = proc_seconds or {}
    total_seconds = sum(float(times.get(r.doc_id, 0.0)) for r in eligible)
    duplicates = workflow.duplicate_doc_ids(eligible)
    review = sum(1 for r in eligible if workflow.requires_human_review(
        r, duplicate=r.doc_id in duplicates))
    po = sum(1 for r in invoices if r.document.get("po_reference"))
    return BusinessMetrics(
        documents=len(eligible),
        invoices=len(invoices),
        critical_found=found,
        critical_possible=possible,
        coverage=(found / possible) if possible else 0.0,
        confidence=(sum(confidences) / len(confidences)) if confidences else None,
        clean_documents=len(eligible) - review,
        review_documents=review,
        po_documents=po,
        non_po_documents=len(invoices) - po,
        total_seconds=total_seconds,
        average_seconds=(total_seconds / len(eligible)) if eligible else 0.0,
    )


def field_coverage_rows(results) -> list[dict]:
    invoices = [
        r for r in managed_results(results)
        if r.document.get("document_type") == "invoice"
    ]
    rows = []
    for field in CRITICAL_FIELDS:
        found = sum(1 for r in invoices if _present(r.document.get(field)))
        total = len(invoices)
        rows.append({
            "Campo crítico": FIELD_LABELS[field],
            "Encontrado": found,
            "Facturas evaluadas": total,
            "Cobertura": "—" if not total else f"{found / total * 100:.0f}%",
        })
    return rows


def _render_metrics(metrics: BusinessMetrics) -> None:
    row1 = st.columns(4)
    row1[0].metric("Documentos procesados", metrics.documents)
    row1[1].metric("Facturas reconocidas", metrics.invoices)
    row1[2].metric("Campos críticos encontrados", metrics.critical_found)
    row1[3].metric("Cobertura de campos críticos", f"{metrics.coverage * 100:.0f}%")
    row2 = st.columns(4)
    row2[0].metric(
        "Confianza promedio",
        "—" if metrics.confidence is None else f"{metrics.confidence * 100:.0f}%",
    )
    row2[1].metric("Sin advertencias", metrics.clean_documents)
    row2[2].metric("A revisar", metrics.review_documents)
    row2[3].metric(
        "Tiempo de procesamiento",
        f"{metrics.total_seconds:.1f} s",
        help=f"Promedio: {metrics.average_seconds:.1f} s por documento.",
    )
    st.caption(
        "Cobertura y confianza describen la extracción; no equivalen a exactitud "
        "contable validada por una persona."
    )


def _render_asis_comparison(metrics: BusinessMetrics) -> None:
    st.markdown("### Del proceso actual a una operación asistida")
    rows = [
        {
            "Proceso actual (AS-IS)": "Recepción y descarga manual desde email",
            "Evidencia en esta sesión": f"{metrics.documents} documento(s) procesado(s)",
            "Valor observable": "Ingreso digital y trazable del documento",
        },
        {
            "Proceso actual (AS-IS)": "Lectura y carga manual de datos de factura",
            "Evidencia en esta sesión": (
                f"{metrics.critical_found}/{metrics.critical_possible} campos críticos encontrados"
            ),
            "Valor observable": "Estructuración automática antes de la revisión humana",
        },
        {
            "Proceso actual (AS-IS)": "Búsqueda manual de OC o contexto interno",
            "Evidencia en esta sesión": (
                f"{metrics.po_documents} ruta PO · {metrics.non_po_documents} ruta non-PO"
            ),
            "Valor observable": "La ausencia de OC se trata como ruta normal, no como error",
        },
        {
            "Proceso actual (AS-IS)": "Validaciones manuales distribuidas",
            "Evidencia en esta sesión": (
                f"{metrics.review_documents} documento(s) con campos a revisar"
            ),
            "Valor observable": "La atención se concentra donde existe incertidumbre",
        },
        {
            "Proceso actual (AS-IS)": "Información dispersa entre email, carpetas y Excel",
            "Evidencia en esta sesión": "Resultados consolidados en una sola vista",
            "Valor observable": "Mayor visibilidad durante el análisis",
        },
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_asis_snapshot() -> None:
    """Datos declarados por la consultora en su mapeo del proceso actual."""
    st.markdown("### Punto de partida declarado por la consultora")
    row1 = st.columns(3)
    row1[0].metric("Volumen actual", "~35 facturas/mes")
    row1[1].metric("Proceso punta a punta", "30 pasos manuales")
    row1[2].metric("Personas involucradas", "2–4")
    row2 = st.columns(2)
    row2[0].metric(
        "Sistemas y canales",
        "5",
        help="Email, SharePoint, Excel de cashflow, Sage ERP y Banco Sabadell.",
    )
    row2[1].metric("Frecuencia de pagos", "Semanal")
    st.caption(
        "Fuente: proceso AS-IS mapeado por la propia consultora. Estos datos no "
        "son una estimación del modelo."
    )


def _render_scope(metrics: BusinessMetrics) -> None:
    st.markdown("### Qué demuestra la evaluación")
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        po_word = "factura" if metrics.po_documents == 1 else "facturas"
        non_po_word = "factura" if metrics.non_po_documents == 1 else "facturas"
        review_word = "documento" if metrics.review_documents == 1 else "documentos"
        st.markdown("#### Con sus facturas")
        st.markdown(
            f"- {metrics.documents} documentos procesados con Google Document AI\n"
            f"- {metrics.coverage * 100:.0f}% de cobertura en campos críticos medibles\n"
            f"- {metrics.po_documents} {po_word} PO y "
            f"{metrics.non_po_documents} {non_po_word} non-PO\n"
            f"- {metrics.review_documents} {review_word} focalizado"
            f"{'s' if metrics.review_documents != 1 else ''} para revisión"
        )
    with c2:
        st.markdown("#### En la Demo completa")
        st.markdown(
            "- Controles C0–C11\n"
            "- Detección de duplicados y diferencias\n"
            "- Maker-checker y cola de excepciones\n"
            "- Aprobación humana antes del pago\n"
            "- Audit trail encadenado"
        )
    with c3:
        st.markdown("#### Requiere integración")
        st.markdown(
            "- Sage ERP\n"
            "- SharePoint y archivo de cashflow\n"
            "- Banco Sabadell\n"
            "- Maestro interno de proyectos, BU y aprobadores"
        )


def _render_executive_readout(metrics: BusinessMetrics) -> None:
    confidence = (
        "sin confianza agregada disponible"
        if metrics.confidence is None
        else f"con una confianza promedio informada de {metrics.confidence * 100:.0f}%"
    )
    st.markdown("### Lectura ejecutiva")
    st.html(
        "<div class='apct-card'>"
        f"En esta sesión, Google Document AI procesó <b>{metrics.documents} documento(s)</b> "
        f"y reconoció <b>{metrics.invoices} factura(s)</b>. Encontró "
        f"<b>{metrics.critical_found} de {metrics.critical_possible} campos críticos</b> "
        f"(<b>{metrics.coverage * 100:.0f}% de cobertura</b>), {confidence}. "
        f"<b>{metrics.clean_documents}</b> documento(s) no presentaron advertencias de campo "
        f"y <b>{metrics.review_documents}</b> concentraron la revisión. "
        "La evidencia respalda la automatización de la recepción, lectura y estructuración "
        "documental; los controles de riesgo, las excepciones y la aprobación previa al pago "
        "se observan en AP Control Tower Demo. Las conexiones con Sage, SharePoint y Banco "
        "Sabadell no forman parte de esta prueba y no se presentan como integraciones activas."
        "</div>"
    )


def render() -> None:
    st.markdown("## Consultar caso de negocio")
    st.caption(
        "Evidencia de esta sesión comparada con el proceso AS-IS mapeado por la consultora."
    )
    session = sess.get_session()
    if not session.results and not session.errors:
        st.info(
            "Todavía no procesaste documentos. Andá a **Probar con mis facturas** "
            "para generar el análisis."
        )
        return

    eligible = managed_results(session.results)
    excluded = len(session.results) - len(eligible)
    if not eligible:
        st.error(
            "Esta sesión no contiene resultados de Google Document AI. Los resultados "
            "del motor local son preliminares y no se utilizan para construir el caso "
            "de negocio. Configurá `google_document_ai_invoice_parser` y volvé a procesar."
        )
        return
    if excluded:
        st.warning(
            f"Se excluyeron {excluded} resultado(s) del motor local. Este análisis utiliza "
            "exclusivamente `google_document_ai_invoice_parser`."
        )

    metrics = calculate_metrics(session.results, session.proc_seconds)
    _render_metrics(metrics)

    _render_asis_snapshot()

    st.markdown("### Cobertura por campo crítico")
    st.dataframe(
        pd.DataFrame(field_coverage_rows(session.results)),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "El AS-IS también requiere leyenda/descripción. El esquema actual todavía no la "
        "expone como campo canónico; se declara como brecha y no se inventa en la cobertura."
    )

    _render_asis_comparison(metrics)
    _render_scope(metrics)
    _render_executive_readout(metrics)

    st.markdown("---")
    sess.render_clear_action()
