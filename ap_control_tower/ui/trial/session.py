"""Estado activo del Trial y respaldo opcional de resultados en PostgreSQL.

Regla dura: todo el contenido real (resultados de extraccion, audit trail) vive
exclusivamente en la sesion mientras se procesa. Si PostgreSQL está configurado,
se respaldan resultados estructurados y auditoría; nunca el PDF. El modelo puro
(``TrialSession`` + funciones) es testeable sin Streamlit; los accesores a
``st.session_state`` son la unica capa acoplada a la UI.

Privacidad: el audit trail NO guarda valores de campos ni contenido del PDF, solo
metadatos (tipo, motor, confianza, cantidad de advertencias).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging

from ...audit import AuditTrail

_KEY = "_trial_session"
_LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TrialSession:
    audit: AuditTrail
    results: list = field(default_factory=list)          # list[PocResult]
    errors: list = field(default_factory=list)           # list[(filename, detalle)]
    proc_seconds: dict = field(default_factory=dict)     # doc_id -> segundos
    processing_seconds: float = 0.0
    created_at: str = field(default_factory=_now)
    file_hashes: dict = field(default_factory=dict)      # doc_id -> sha256
    sources: dict = field(default_factory=dict)          # doc_id -> canal
    persistence_error: str | None = None


def new_session() -> TrialSession:
    audit = AuditTrail(commit="trial-session")
    audit.add(agent="trial", action="sesion-iniciada")
    return TrialSession(audit=audit)


def record_intake(session: TrialSession, canal: str, cantidad: int) -> None:
    """Registra una ingesta por canal (carga-manual | gmail) sin contenido."""
    session.audit.add(agent="trial", action="ingesta", result=canal,
                      evidence={"canal": canal, "documentos": cantidad})


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


def add_document(session: TrialSession, result, seconds: float = 0.0,
                 file_hash: str | None = None,
                 source: str = "carga-manual") -> None:
    """Agrega UN documento procesado con su tiempo y un evento de auditoria
    (solo metadatos: nunca valores de campos ni contenido del PDF)."""
    session.results.append(result)
    session.proc_seconds[result.doc_id] = round(max(0.0, float(seconds)), 3)
    session.processing_seconds += max(0.0, float(seconds))
    if file_hash:
        session.file_hashes[result.doc_id] = file_hash
    session.sources[result.doc_id] = source
    session.audit.add(
        agent="trial",
        action="documento-procesado",
        invoice_id=result.doc_id,
        result=("con-advertencias" if result.warnings else "ok"),
        evidence={
            "tipo": result.document.get("document_type"),
            "motor": result.engine,
            "confianza": str(result.confidence),
            "advertencias": len(result.warnings),
            "paginas": result.pages,
            "segundos": round(max(0.0, float(seconds)), 2),
        },
    )


def add_error(session: TrialSession, filename: str, detalle: str,
              seconds: float = 0.0) -> None:
    """Registra un documento que fallo el procesamiento (estado 'Error de
    procesamiento'). El detalle se trunca; no se guarda contenido del PDF."""
    session.errors.append((filename, detalle))
    session.processing_seconds += max(0.0, float(seconds))
    session.audit.add(
        agent="trial",
        action="error-procesamiento",
        invoice_id=filename,
        result="error",
        evidence={"detalle": (detalle or "")[:160]},
    )


def add_processing_time(session: TrialSession, seconds: float) -> None:
    """Acumula el tiempo real de procesamiento medido dentro de esta sesion."""
    session.processing_seconds += max(0.0, float(seconds))


def record_event(session: TrialSession, action: str, evidence: dict | None = None) -> None:
    session.audit.add(agent="trial", action=action, evidence=evidence or {})


def persist(session: TrialSession) -> bool:
    """Guarda la sesión si PostgreSQL está disponible; degrada sin romper UI."""
    try:
        from . import persistence_bridge
        if not persistence_bridge.available():
            return False
        persistence_bridge.save(session)
        session.persistence_error = None
        return True
    except Exception as exc:
        _LOGGER.exception("No se pudo persistir la corrida Trial")
        session.persistence_error = str(exc)[:240]
        return False


def persistence_available() -> bool:
    try:
        from . import persistence_bridge
        return persistence_bridge.available()
    except Exception:
        return False


def saved_runs(limit: int = 25) -> list[dict]:
    from . import persistence_bridge
    return persistence_bridge.list_runs(limit=limit)


def load_saved_run(run_id: str):
    from . import persistence_bridge
    return persistence_bridge.load(run_id)


def delete_saved_run(run_id: str) -> bool:
    from . import persistence_bridge
    return persistence_bridge.delete(run_id)


# ------------------------------------------------------------------ Streamlit
def get_session() -> TrialSession:
    import streamlit as st

    if _KEY not in st.session_state:
        st.session_state[_KEY] = new_session()
    return st.session_state[_KEY]


def session_keys_to_clear(all_keys) -> list:
    """Claves de session_state que borra 'Finalizar y borrar' (logica pura)."""
    return [k for k in all_keys if str(k) == _KEY or str(k).startswith("_trial_")]


def reset_session() -> None:
    """Elimina resultados, documentos y audit trail de la sesion."""
    import streamlit as st

    for key in session_keys_to_clear(list(st.session_state.keys())):
        st.session_state.pop(key, None)


def _clear_and_rerun() -> None:
    import streamlit as st

    reset_session()
    st.rerun()


def render_sidebar_actions() -> None:
    import streamlit as st

    session = get_session()
    st.sidebar.markdown("---")
    stored = persistence_available()
    st.sidebar.caption(
        f"Sesión iniciada · {len(session.results)} documento(s)"
        + (" · historial activo" if stored else " · solo memoria"))
    if st.sidebar.button("🗑  Finalizar sesión actual", use_container_width=True,
                         key="_trial_clear_sidebar"):
        _clear_and_rerun()
    if session.persistence_error:
        st.sidebar.warning("No se pudo actualizar el historial. La sesión actual sigue activa.")
    elif stored:
        st.sidebar.caption("Se guardan extracción, métricas y auditoría. El PDF se descarta.")
    else:
        st.sidebar.caption("Sin base configurada: al cerrar, los resultados desaparecen.")


def render_clear_action() -> None:
    """Accion visible de borrado para el area principal (p. ej. Resultados)."""
    import streamlit as st

    if st.button("🗑  Finalizar sesión actual", use_container_width=True,
                 key="_trial_clear_main"):
        _clear_and_rerun()
