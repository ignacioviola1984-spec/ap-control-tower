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
    review_decisions: dict = field(default_factory=dict) # doc_id -> decisión humana
    approval_decisions: dict = field(default_factory=dict) # doc_id -> propuesta pago
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
        if _already_present(session, r.doc_id):
            _record_duplicate_omitted(session, r.doc_id)
            continue
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


def _already_present(session: TrialSession, doc_id: str,
                     file_hash: str | None = None) -> bool:
    if any(str(item.doc_id) == str(doc_id) for item in session.results):
        return True
    return bool(file_hash and file_hash in session.file_hashes.values())


def _record_duplicate_omitted(session: TrialSession, doc_id: str) -> None:
    session.audit.add(
        agent="trial", action="documento-repetido-omitido", invoice_id=doc_id,
        result="omitido", evidence={"motivo": "ya-presente-en-la-corrida"})


def add_document(session: TrialSession, result, seconds: float = 0.0,
                 file_hash: str | None = None,
                 source: str = "carga-manual") -> bool:
    """Agrega UN documento procesado con su tiempo y un evento de auditoria
    (solo metadatos: nunca valores de campos ni contenido del PDF)."""
    if _already_present(session, result.doc_id, file_hash):
        _record_duplicate_omitted(session, result.doc_id)
        return False
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
    return True


def repair_duplicates(session: TrialSession) -> int:
    """Repara sesiones antiguas contaminadas por una importación repetida."""
    from .workflow import unique_results

    before = len(session.results)
    session.results = unique_results(session.results)
    removed = before - len(session.results)
    if removed:
        valid_ids = {str(result.doc_id) for result in session.results}
        session.proc_seconds = {
            key: value for key, value in session.proc_seconds.items()
            if str(key) in valid_ids}
        session.file_hashes = {
            key: value for key, value in session.file_hashes.items()
            if str(key) in valid_ids}
        session.sources = {
            key: value for key, value in session.sources.items()
            if str(key) in valid_ids}
        # La versión defectuosa sumaba nuevamente el tiempo de los mismos PDF.
        # Al reparar, el total vuelve a derivarse de los documentos únicos.
        session.processing_seconds = round(
            sum(float(value) for value in session.proc_seconds.values()), 3)
        session.audit.add(
            agent="trial", action="sesion-deduplicada", result="reparada",
            evidence={"documentos_repetidos_eliminados": removed})
    return removed


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


def _result_by_id(session: TrialSession, doc_id: str):
    for result in session.results:
        if result.doc_id == doc_id:
            return result
    raise ValueError("El documento ya no está disponible en esta corrida.")


def confirm_review(session: TrialSession, doc_id: str, reviewer: str,
                   updates: dict, note: str = "") -> dict:
    from . import workflow

    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("Ingresá el nombre de quien realiza la revisión.")
    result = _result_by_id(session, doc_id)
    clean = workflow.normalized_updates(updates)
    changed = [field for field, value in clean.items()
               if str(result.document.get(field) or "") != str(value or "")]
    candidate = {**result.document, **clean}
    if candidate.get("document_type") == "invoice":
        missing = workflow.missing_critical_fields(candidate)
        if missing:
            raise ValueError("Todavía faltan campos críticos: " + ", ".join(missing))
    result.document.update(clean)
    decision = {"status": "confirmed", "actor": reviewer,
                "note": (note or "").strip()[:500], "fields_changed": changed,
                "timestamp": workflow.now_iso()}
    session.review_decisions[doc_id] = decision
    session.audit.add(
        agent=reviewer, action="revision-humana-confirmada", invoice_id=doc_id,
        result="confirmed", evidence={"campos_corregidos": changed,
                                      "motivo_informado": bool(decision["note"])},
    )
    return decision


def retain_review(session: TrialSession, doc_id: str, reviewer: str, note: str) -> dict:
    from . import workflow

    reviewer = (reviewer or "").strip()
    note = (note or "").strip()
    if not reviewer:
        raise ValueError("Ingresá el nombre de quien realiza la revisión.")
    if not note:
        raise ValueError("Indicá el motivo de la retención.")
    _result_by_id(session, doc_id)
    decision = {"status": "retained", "actor": reviewer, "note": note[:500],
                "fields_changed": [], "timestamp": workflow.now_iso()}
    session.review_decisions[doc_id] = decision
    session.audit.add(agent=reviewer, action="documento-retenido-en-revision",
                      invoice_id=doc_id, result="retained",
                      evidence={"motivo_informado": True})
    return decision


def decide_payment_proposal(session: TrialSession, doc_ids: list[str], approver: str,
                            status: str, note: str = "") -> list[dict]:
    from . import workflow

    approver = (approver or "").strip()
    note = (note or "").strip()
    if not approver:
        raise ValueError("Ingresá el nombre de quien toma la decisión.")
    if status not in {"approved", "rejected", "excluded"}:
        raise ValueError("Decisión de pago inválida.")
    if not doc_ids:
        raise ValueError("Seleccioná al menos una factura.")
    if status == "rejected" and not note:
        raise ValueError("El rechazo requiere un motivo.")

    states = {row["result"].doc_id: row
              for row in workflow.approval_rows(
                  session.results, session.review_decisions, session.approval_decisions)}
    decisions = []
    for doc_id in doc_ids:
        row = states.get(doc_id)
        if row is None:
            raise ValueError(f"Documento no encontrado: {doc_id}")
        if status == "approved" and row["status"] != "eligible":
            raise ValueError(f"{doc_id} no es elegible: " + ", ".join(row["reasons"]))
        reviewer = (session.review_decisions.get(doc_id) or {}).get("actor")
        if reviewer and reviewer.casefold() == approver.casefold():
            raise ValueError("Maker-checker: quien revisó no puede aprobar la misma factura.")
        decision = {"status": status, "actor": approver, "note": note[:500],
                    "timestamp": workflow.now_iso()}
        session.approval_decisions[doc_id] = decision
        session.audit.add(
            agent=approver,
            action=("aprobada-para-propuesta-pago" if status == "approved"
                    else ("rechazada-para-propuesta-pago" if status == "rejected"
                          else "excluida-de-propuesta-pago")),
            invoice_id=doc_id, result=status,
            evidence={"motivo_informado": bool(note),
                      "no_libera_dinero": True},
        )
        decisions.append(decision)
    return decisions


def request_classification_review(session: TrialSession, doc_id: str,
                                  actor: str, note: str) -> dict:
    from . import workflow

    actor = (actor or "").strip()
    note = (note or "").strip()
    if not actor:
        raise ValueError("Ingresá el nombre de quien solicita la revisión.")
    if not note:
        raise ValueError("Indicá por qué la clasificación debe revisarse.")
    _result_by_id(session, doc_id)
    decision = {"status": "requested", "actor": actor, "note": note[:500],
                "fields_changed": [], "timestamp": workflow.now_iso()}
    session.review_decisions[doc_id] = decision
    session.audit.add(
        agent=actor, action="revision-clasificacion-solicitada",
        invoice_id=doc_id, result="requested",
        evidence={"motivo_informado": True, "no_cambia_tipo_automaticamente": True},
    )
    return decision


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


def resume_saved_run(run_id: str) -> TrialSession:
    """Recupera una corrida estructurada para continuar revisión/aprobación."""
    import streamlit as st

    stored = load_saved_run(run_id)
    if stored is None:
        raise ValueError("La corrida guardada ya no existe.")
    active = TrialSession(
        audit=stored.audit, results=list(stored.results), errors=list(stored.errors),
        proc_seconds=dict(stored.proc_seconds),
        processing_seconds=float(stored.processing_seconds),
        created_at=stored.created_at.isoformat(), file_hashes=dict(stored.file_hashes),
        sources=dict(stored.sources),
        review_decisions=dict(stored.review_decisions),
        approval_decisions=dict(stored.approval_decisions),
    )
    st.session_state[_KEY] = active
    return active


def delete_saved_run(run_id: str) -> bool:
    from . import persistence_bridge
    return persistence_bridge.delete(run_id)


# ------------------------------------------------------------------ Streamlit
def get_session() -> TrialSession:
    import streamlit as st

    if _KEY not in st.session_state:
        st.session_state[_KEY] = new_session()
    session = st.session_state[_KEY]
    if repair_duplicates(session):
        persist(session)
    return session


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
    if session.persistence_error:
        st.sidebar.warning("No se pudo actualizar el historial. La sesión actual sigue activa.")
    elif stored:
        st.sidebar.caption("Se guardan extracción, métricas y auditoría. El PDF se descarta.")
    else:
        st.sidebar.caption("Sin base configurada: al cerrar, los resultados desaparecen.")


def render_sidebar_end_session() -> None:
    """Acción final del lateral; reemplaza el antiguo enlace a la Demo."""
    import streamlit as st

    st.sidebar.markdown("---")
    if st.sidebar.button("🗑  Finalizar sesión actual", use_container_width=True,
                         key="_trial_clear_sidebar"):
        _clear_and_rerun()


def render_clear_action() -> None:
    """Accion visible de borrado para el area principal (p. ej. Resultados)."""
    import streamlit as st

    if st.button("🗑  Finalizar sesión actual", use_container_width=True,
                 key="_trial_clear_main"):
        _clear_and_rerun()
