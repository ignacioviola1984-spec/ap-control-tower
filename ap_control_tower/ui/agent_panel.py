"""Panel conversacional contextual embebido en Documentos y Revisión humana."""

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
from .trial import session as sess


_SUGGESTIONS = {
    "¿Por qué requiere revisión?": "Explicá por qué este documento requiere revisión.",
    "Resumir evidencia": "Resumí la evidencia disponible para revisar este documento.",
    "¿Qué debería verificar?": "Sugerí qué debería verificar el revisor a continuación.",
    "Estado del proveedor": "Informá el estado del maestro y la vinculación del proveedor.",
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


@st.fragment
def render_document_agent(active, result, *, page_key: str) -> None:
    settings = AgentSettings.from_env()
    st.subheader("Asistente para este documento")
    st.caption(
        "Explica motivos y evidencia, y sugiere próximos pasos. "
        "No modifica datos ni toma decisiones."
    )

    unavailable = settings.availability_message()
    if unavailable:
        st.info(unavailable, icon=":material/smart_toy:")
        return

    key = _conversation_key(active, result, page_key)
    conversations = _all_conversations()
    messages = conversations.setdefault(key, [])

    with st.container(border=True):
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
            with st.spinner("Consultando evidencia controlada…"):
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
                    response = (
                        "No pude responder en este momento. La revisión humana y "
                        "los controles del documento siguen disponibles."
                    )
                    st.error(response, icon=":material/error:")
                else:
                    audit_answer(active, str(result.doc_id), answer)
                    sess.persist(active)
                    response = answer.text
                    st.write(response)
                    st.caption(
                        "Respuesta informativa de solo lectura · "
                        f"{answer.input_tokens + answer.output_tokens:,} tokens"
                    )
        messages.append({"role": "assistant", "content": response})
        _trim(messages)
