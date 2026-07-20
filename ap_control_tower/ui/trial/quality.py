"""Calidad medida: evidencia del framework de evals del AP Control Tower.

La vista no calcula nada en vivo: muestra los resultados de las corridas de
evaluación contra el golden dataset (documentos con resultado esperado definido
a mano antes de procesar). La fuente es evals/quality_summary.json, que se
regenera con el comparador al cerrar cada corrida.

Principio de honestidad (mismo criterio que el caso de negocio): se muestran
métricas y metodología, nunca el dataset crudo ni afirmaciones sin corrida que
las respalde.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st


def _summary_path() -> Path | None:
    """evals/quality_summary.json relativo a la raíz del repo, si existe."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "evals" / "quality_summary.json"
        if candidate.is_file():
            return candidate
    return None


def _method_note(text: str) -> None:
    st.html(f"<div class='apct-method-note'>{text}</div>")


def _load_summary() -> dict | None:
    path = _summary_path()
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def render() -> None:
    st.title("Calidad medida")
    st.markdown(
        "Cómo sabemos que el sistema funciona: cada versión se evalúa contra un "
        "**golden dataset** (documentos cuyo resultado correcto se definió a mano, "
        "antes de procesarlos). Las métricas y sus limitaciones definen el criterio "
        "de aceptación antes de desplegar."
    )

    summary = _load_summary()
    if summary is None:
        st.info("Todavía no hay resultados de evaluación publicados en esta build.")
        return

    golden = summary.get("golden_dataset", {})
    comp = golden.get("composicion", {})
    st.subheader("El banco de pruebas")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Documentos etiquetados", golden.get("documentos", "—"))
    col2.metric("Facturas reales", comp.get("reales", "—"))
    col3.metric("Documentos públicos", comp.get("publicas", "—"))
    col4.metric("Sintéticos de estrés", comp.get("sinteticas", "—"))
    _method_note(
        "Casos borde incluidos a propósito: " + str(golden.get("casos_borde", "")) +
        ". Un eval sin casos diseñados para fallar no prueba nada."
    )

    corridas = summary.get("corridas", [])
    if len(corridas) >= 2:
        antes, despues = corridas[0], corridas[-1]
        st.subheader("El ciclo de mejora, medido")
        st.markdown(
            f"La política de la corrida **{antes['id']}** ({antes['politica']}) "
            f"derivaba a revisión humana el **{antes['derivacion_pct']:.0f}%** de los "
            f"documentos. Se rediseñó el criterio y la corrida **{despues['id']}** "
            f"({despues['politica']}) lo bajó al **{despues['derivacion_pct']:.1f}%**, "
            "con una reducción material de trabajo manual. Los errores remanentes y "
            "sus mitigantes se muestran de forma explícita."
        )
        col1, col2, col3 = st.columns(3)
        col1.metric(
            "Derivación a revisión humana",
            f"{despues['derivacion_pct']:.1f}%",
            f"{despues['derivacion_pct'] - antes['derivacion_pct']:+.1f} pts",
            delta_color="inverse",
        )
        col2.metric(
            "Exactitud de ruteo",
            f"{despues['ruteo_exactitud_pct']:.1f}%",
            f"{despues['ruteo_exactitud_pct'] - antes['ruteo_exactitud_pct']:+.1f} pts",
        )
        col3.metric(
            "Exactitud de extracción",
            f"{despues['extraccion_exactitud_pct']:.1f}%",
            f"{despues['extraccion_exactitud_pct'] - antes['extraccion_exactitud_pct']:+.1f} pts",
        )
        nota = despues.get("nota_recall")
        if nota:
            _method_note("Transparencia: " + nota)

    policy_replays = summary.get("policy_replays", [])
    if policy_replays:
        replay = policy_replays[-1]
        run2 = corridas[-1] if corridas else {}
        st.subheader("Run3: los fixes revalidados sin reprocesar PDFs")
        st.markdown(
            f"El **{replay['id']}** volvió a ejecutar la política actual sobre las "
            f"**{replay['documentos']} extracciones persistidas** de run2. Validó el "
            "circuito de reglas y ruteo sobre toda la población disponible con "
            f"**{replay['llamadas_document_ai']} llamadas a Document AI**."
        )
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(
            "Exactitud de ruteo",
            f"{replay['ruteo_exactitud_pct']:.1f}%",
            f"{replay['ruteo_exactitud_pct'] - run2.get('ruteo_exactitud_pct', replay['ruteo_exactitud_pct']):+.1f} pts",
        )
        col2.metric(
            "Recall de derivación",
            f"{replay['recall_derivacion_pct']:.1f}%",
            f"{replay['recall_derivacion_pct'] - run2.get('recall_derivacion_pct', replay['recall_derivacion_pct']):+.1f} pts",
        )
        col3.metric(
            "Falsos negativos",
            str(replay["falsos_negativos"]),
            "-1 vs run2",
            delta_color="inverse",
        )
        col4.metric(
            f"Revisión humana ({replay['derivacion_cantidad']}/{replay['documentos']})",
            f"{replay['derivacion_pct']:.1f}%",
            f"{replay['derivacion_pct'] - replay['run2_recalculado_sobre_golden_pct']:+.1f} pts",
            delta_color="inverse",
        )

        fixes = replay.get("fixes_validados", [])
        if fixes:
            st.markdown(
                "**Qué corrigió la iteración**\n\n" +
                "\n".join(f"- {item}" for item in fixes)
            )
        st.markdown(
            "**Valor comercial de esta evidencia:** muestra el ciclo completo de "
            "mejora —un error detectado por evals se transforma en una regla, se "
            "repite contra los 106 casos y se mide nuevamente— sin pagar otra vez "
            "por una extracción que no cambió."
        )

        limitations = replay.get("limitaciones", [])
        if limitations:
            st.markdown(
                "**Qué no demuestra este replay**\n\n" +
                "\n".join(f"- {item}" for item in limitations)
            )
        smoke = replay.get("smoke", {})
        if smoke:
            _method_note(
                "Decisión de costo: smoke cloud " + str(smoke.get("estado", "")) +
                ". " + str(smoke.get("motivo", "")) + ". Los " +
                str(smoke.get("candidatos_versionados", "")) +
                " documentos candidatos quedan versionados para ese momento."
            )
        _method_note(
            "Alcance: run3 revalida la política sobre las extracciones guardadas de "
            "run2; no vuelve a medir Document AI. Por eso la exactitud de extracción "
            f"se mantiene como evidencia de run2 ({replay.get('extraccion_base', 'no re-medida')})."
        )

    st.subheader("El hallazgo que cambió el diseño")
    st.markdown(summary.get("hallazgo_central", ""))
    calibracion = summary.get("calibracion_run1")
    if calibracion:
        cal = pd.DataFrame(calibracion)
        cal.columns = ["Confianza reportada por el extractor", "Exactitud real medida (%)"]
        st.dataframe(cal, hide_index=True, use_container_width=True)
        _method_note(
            "Medido campo por campo contra el ground truth del golden dataset. "
            "La relación invertida es la razón por la que el sistema no usa el "
            "score de confianza para decidir derivaciones."
        )

    campos = summary.get("extraccion_por_campo_run2")
    if campos:
        st.subheader("Exactitud de extracción por campo (run2)")
        df = pd.DataFrame(campos)
        df.columns = ["Campo", "Exactitud (%)"]
        st.dataframe(df, hide_index=True, use_container_width=True)

    st.subheader("Método, en cuatro reglas")
    st.markdown(
        "1. **Ground truth primero:** el resultado esperado de cada documento se define "
        "a mano antes de procesarlo; nunca se copia de lo que el sistema devolvió.\n"
        "2. **Golden dataset congelado y versionado:** las corridas son comparables "
        "entre sí; toda corrección al dataset queda documentada.\n"
        "3. **Regresión obligatoria:** cualquier cambio (prompt, umbral, modelo, regla) "
        "se recorre contra el mismo dataset antes de desplegarse.\n"
        "4. **Falsos aprobados como métrica bloqueante:** un documento que debía "
        "frenarse y avanzó invalida la corrida, aunque el resto mejore."
    )
    _method_note(
        "Estas métricas describen las corridas de evaluación citadas, con la "
        "composición de documentos declarada arriba. No son una promesa de "
        "rendimiento sobre cualquier volumen futuro: son la evidencia de que el "
        "sistema se mide, y de cómo mejora cuando algo falla."
    )
