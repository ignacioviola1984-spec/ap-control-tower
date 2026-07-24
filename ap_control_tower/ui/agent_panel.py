"""Panel conversacional contextual embebido en Documentos y Revisión humana.

Presentación estructurada deliberada: los **riesgos**, la **evidencia** y los
**controles consultados** los produce el sistema y se muestran siempre, incluso
con la IA apagada. El texto del modelo va aparte, marcado en índigo, que en este
producto significa una única cosa: lo escribió la IA. Mezclar ambas cosas en un
mismo bloque haría que una explicación generada parezca un control ejecutado.
"""

from __future__ import annotations

import streamlit as st

from ..agent.config import AgentSettings
from ..agent.service import (
    AgentServiceError,
    AgentUnavailable,
    answer_question,
    audit_answer,
    audit_error,
    create_openai_client,
)
from ..agent.tools import ReadOnlyDocumentTools
from . import design
from .trial import session as sess
from .trial import workflow


_SUGGESTIONS = {
    "¿Por qué requiere revisión?": "Explicá por qué este documento requiere revisión.",
    "Resumir evidencia": "Resumí la evidencia disponible para revisar este documento.",
    "¿Qué debería verificar?": "Sugerí qué debería verificar el revisor a continuación.",
    "Estado del proveedor": "Informá el estado del maestro y la vinculación del proveedor.",
}

#: Nombre legible de cada herramienta de solo lectura del asistente.
TOOL_LABELS = {
    "get_document_context": "Datos extraídos del documento",
    "explain_review_reasons": "Motivos de revisión",
    "summarize_document_evidence": "Evidencia y advertencias",
    "suggest_reviewer_actions": "Acciones sugeridas al revisor",
    "get_vendor_master_status": "Maestro de proveedores",
    "get_document_text": "Texto del documento",
}


@st.cache_resource(show_spinner=False)
def _openai_client():
    return create_openai_client()


def _conversation_key(active, result, page_key: str) -> str:
    return f"{active.audit.run_id}:{result.doc_id}:{page_key}"


def _all_conversations() -> dict[str, list[dict[str, str]]]:
    return st.session_state.setdefault("_ap_agent_conversations", {})


def _trim(messages: list[dict[str, str]]) -> None:
    del messages[:-12]


_COPILOT_CSS = """
<style>
/* Identidad de IA: índigo reservado. Ver este acento significa siempre que el
   contenido lo produjo el copiloto, nunca un control determinista. */
.ap-copilot-head {
  display: flex; align-items: center; gap: 8px; margin-bottom: 2px;
}
.ap-copilot-badge {
  display: inline-flex; align-items: center; gap: 5px;
  background: #EFEBFF; color: #6D4AFF; border: 1px solid #D6CCFF;
  border-radius: 999px; padding: 1px 9px; font-size: 11.5px; font-weight: 700;
}
.ap-copilot-title { font-size: 15px; font-weight: 650; color: #0F1B2D; }
.st-key-ap_copilot [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
  background: #FAF8FF;
}
.ap-sysblock {
  font-size: 13px; line-height: 1.5;
}
.ap-sysblock h6 {
  font-size: 11.5px; font-weight: 700; letter-spacing: .05em;
  text-transform: uppercase; color: #5A6B85; margin: 10px 0 3px 0;
}
.ap-sysblock ul { margin: 0; padding-left: 18px; }
</style>
"""


def _copilot_header(available: bool) -> None:
    estado = ("disponible", "#127A4B") if available else ("no disponible", "#9A5B00")
    st.html(
        _COPILOT_CSS
        + '<div class="ap-copilot-head">'
        '<span class="ap-copilot-badge">'
        '<span class="material-symbols-rounded" style="font-size:14px;">'
        'auto_awesome</span> IA</span>'
        '<span class="ap-copilot-title">Copiloto AP</span>'
        f'<span style="margin-left:auto;font-size:12px;color:{estado[1]};'
        f'font-weight:600;">● {estado[0]}</span></div>'
    )
    st.caption(
        "Explica motivos y evidencia; sugiere próximos pasos. Es informativo y "
        "de solo lectura: no modifica datos ni toma decisiones."
    )


def deterministic_briefing(active, result) -> dict:
    """Riesgos, evidencia y próximo paso, calculados por el sistema.

    Función pura sobre los datos ya extraídos: sigue siendo exacta con la IA
    apagada, y es lo que se muestra cuando el modelo no está disponible.
    """
    duplicates = workflow.duplicate_doc_ids(active.results)
    reasons = workflow.review_reasons(
        result, duplicate=str(result.doc_id) in duplicates
    )
    missing = workflow.missing_critical_fields(result.document)
    evidencia = [
        "Extracción: " + ("Google Document AI"
                          if result.engine == "google_document_ai_invoice_parser"
                          else "motor local controlado"),
        "Campos críticos: " + ("completos" if not missing
                               else "faltan " + ", ".join(missing)),
    ]
    resolution = getattr(active, "supplier_resolutions", {}).get(str(result.doc_id))
    if resolution:
        evidencia.append("Maestro de proveedores: " + {
            "matched": "proveedor vinculado",
            "not_found": "proveedor no dado de alta",
            "inactive": "proveedor dado de baja",
            "ambiguous": "vinculación ambigua",
        }.get(resolution.get("status"), str(resolution.get("status") or "—")))
    else:
        evidencia.append("Maestro de proveedores: sin maestro aplicado")

    if reasons:
        proximo = "Resolver los motivos listados y confirmar los datos, o retener."
    else:
        proximo = "Sin motivos pendientes: puede avanzar al gate de pago."
    return {"riesgos": reasons, "evidencia": evidencia, "proximo": proximo}


def _render_system_block(briefing: dict) -> None:
    """Bloque del SISTEMA: sin acento índigo, porque no lo escribió la IA."""
    riesgos = "".join(f"<li>{design.esc(item)}</li>" for item in briefing["riesgos"])
    evidencia = "".join(f"<li>{design.esc(item)}</li>" for item in briefing["evidencia"])
    st.html(
        '<div class="ap-sysblock">'
        "<h6>Riesgos detectados</h6>"
        + (f"<ul>{riesgos}</ul>" if riesgos
           else "<p>Ninguno para este documento.</p>")
        + "<h6>Evidencia</h6>"
        + f"<ul>{evidencia}</ul>"
        + "<h6>Próximo paso</h6>"
        + f"<p>{design.esc(briefing['proximo'])}</p>"
        + "</div>"
    )


def _render_answer_meta(answer) -> None:
    """Controles consultados y limitaciones de la respuesta del modelo."""
    usados = [TOOL_LABELS.get(name, name) for name in answer.tools_used]
    st.html(
        '<div class="ap-sysblock"><h6>Controles consultados</h6>'
        + ("<ul>" + "".join(f"<li>{design.esc(item)}</li>" for item in usados) + "</ul>"
           if usados else "<p>Ninguno: la respuesta no consultó evidencia.</p>")
        + "</div>"
    )
    st.caption(
        "Limitaciones: la respuesta es informativa, puede contener errores y no "
        "sustituye la verificación humana. No accede a datos enmascarados. · "
        f"{answer.input_tokens + answer.output_tokens:,} tokens"
    )


@st.fragment
def render_document_agent(active, result, *, page_key: str) -> None:
    settings = AgentSettings.from_env()
    unavailable = settings.availability_message()
    _copilot_header(available=unavailable is None)

    briefing = deterministic_briefing(active, result)

    if unavailable:
        # Degradación: sin modelo, el panel sigue siendo útil. Lo que se pierde
        # es la explicación en lenguaje natural, no la evidencia.
        st.info(unavailable, icon=":material/smart_toy:")
        with st.container(border=True, key="ap_copilot"):
            _render_system_block(briefing)
        return

    key = _conversation_key(active, result, page_key)
    conversations = _all_conversations()
    messages = conversations.setdefault(key, [])

    with st.container(border=True, key="ap_copilot"):
        with st.expander("Riesgos, evidencia y próximo paso",
                         icon=":material/rule:", expanded=not messages):
            _render_system_block(briefing)

        if not messages:
            selected = st.pills(
                "Consultas sugeridas",
                list(_SUGGESTIONS),
                label_visibility="collapsed",
                key=f"agent_suggestions_{key}",
            )
        else:
            selected = None
            if st.button(
                "Limpiar conversación",
                icon=":material/delete_sweep:",
                key=f"agent_clear_{key}",
            ):
                conversations[key] = []
                st.rerun(scope="fragment")

        for message in messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        prompt = st.chat_input(
            "Preguntá sobre este documento",
            submit_mode="disable",
            key=f"agent_prompt_{key}",
        )
        prompt = _SUGGESTIONS.get(selected) if selected else prompt
        if not prompt:
            st.caption(
                "No ingreses cuentas bancarias, claves ni otros datos sensibles."
            )
            return

        prior_history = list(messages)
        messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            # El asistente resuelve en varias rondas de herramientas y el texto
            # sólo existe cuando termina la última: no hay tokens que emitir
            # progresivamente, así que se informa el avance real en vez de
            # animar un texto ya completo con st.write_stream.
            answer = None
            with st.status("Consultando evidencia controlada…",
                           expanded=False) as estado:
                try:
                    client = _openai_client()
                    answer = answer_question(
                        prompt,
                        prior_history,
                        ReadOnlyDocumentTools(active, result),
                        client=client,
                        settings=settings,
                    )
                except (AgentUnavailable, AgentServiceError) as exc:
                    audit_error(active, str(result.doc_id), exc)
                    sess.persist(active)
                    estado.update(label="Sin respuesta del asistente",
                                  state="error")
                    response = (
                        "No pude responder en este momento. La revisión humana y "
                        "los controles del documento siguen disponibles."
                    )
                else:
                    audit_answer(active, str(result.doc_id), answer)
                    sess.persist(active)
                    estado.update(
                        label=f"Respuesta lista · {len(answer.tools_used)} control(es) consultados",
                        state="complete",
                    )
                    response = answer.text
            if answer is None:
                st.error(response, icon=":material/error:")
            else:
                st.write(response)
                _render_answer_meta(answer)
        messages.append({"role": "assistant", "content": response})
        _trim(messages)


__all__ = ["TOOL_LABELS", "deterministic_briefing", "render_document_agent"]
