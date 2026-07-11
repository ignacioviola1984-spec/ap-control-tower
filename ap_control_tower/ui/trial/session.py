"""Estado de la sesion del trial: SOLO en memoria (st.session_state).

Regla dura: todo el contenido real (resultados de extraccion, audit trail) vive
exclusivamente en la sesion. Sin cache global, disco, base ni GCS. El modelo puro
(``TrialSession`` + funciones) es testeable sin Streamlit; los accesores a
``st.session_state`` son la unica capa acoplada a la UI.

Privacidad: el audit trail NO guarda valores de campos ni contenido del PDF, solo
metadatos (tipo, motor, confianza, cantidad de advertencias).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ...audit import AuditTrail

_KEY = "_trial_session"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TrialSession:
    audit: AuditTrail
    results: list = field(default_factory=list)   # list[PocResult]
    created_at: str = field(default_factory=_now)


def new_session() -> TrialSession:
    return TrialSession(audit=AuditTrail(commit="trial-session"))


def add_results(session: TrialSession, results) -> None:
    """Agrega resultados de extraccion y registra un evento de auditoria por doc
    (sin contenido del documento: solo metadatos)."""
    for r in results:
        session.results.append(r)
        session.audit.add(
            agent="trial",
            action="documento-procesado",
            invoice_id=r.doc_id,
            result=("con-advertencias" if r.warnings else "ok"),
            evidence={
                "tipo": r.document.get("document_type"),
                "motor": r.engine,
                "confianza": str(r.confidence),
                "advertencias": len(r.warnings),
                "paginas": r.pages,
            },
        )


def record_event(session: TrialSession, action: str, evidence: dict | None = None) -> None:
    session.audit.add(agent="trial", action=action, evidence=evidence or {})


# ------------------------------------------------------------------ Streamlit
def get_session() -> TrialSession:
    import streamlit as st

    if _KEY not in st.session_state:
        st.session_state[_KEY] = new_session()
    return st.session_state[_KEY]


def reset_session() -> None:
    """Elimina resultados, documentos y audit trail de la sesion."""
    import streamlit as st

    for key in list(st.session_state.keys()):
        if str(key) == _KEY or str(key).startswith("_trial_"):
            st.session_state.pop(key, None)


def render_sidebar_actions() -> None:
    import streamlit as st

    session = get_session()
    st.sidebar.markdown("---")
    st.sidebar.caption(f"Sesión iniciada · {len(session.results)} documento(s) en memoria")
    if st.sidebar.button("🗑  Finalizar y borrar esta sesión", use_container_width=True):
        reset_session()
        st.rerun()
    st.sidebar.caption("Nada se guarda: al cerrar la pestaña o borrar, todo desaparece.")
