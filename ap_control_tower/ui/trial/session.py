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
    supplier_master: object | None = field(default=None, repr=False)
    supplier_master_summary: dict = field(default_factory=dict)
    supplier_resolutions: dict = field(default_factory=dict) # doc_id -> match no sensible
    #: Altas de proveedor hechas en la sesión, pendientes de llegar a Sage.
    #: Viajan con el lote de pago: el pago a un proveedor nuevo no sirve de nada
    #: si su ficha no está dada de alta en el ERP.
    pending_vendors: list = field(default_factory=list)
    persistence_error: str | None = None


def new_session() -> TrialSession:
    audit = AuditTrail(commit="pilot-session")
    audit.add(agent="sistema", action="sesion-iniciada")
    session = TrialSession(audit=audit)
    _apply_provisioned_master(session)
    return session


def _apply_provisioned_master(session: TrialSession) -> None:
    """Aplica el maestro instalado en el sistema, sin intervención del usuario.

    El maestro es configuración de la instalación, no un archivo que el operador
    deba recordar subir en cada sesión. Si no hay ninguno instalado la sesión
    arranca igual: cada documento queda advertido por falta de conciliación.
    """
    import os

    from ...sage.provisioning import load_provisioned_vendor_master

    # Los documentos del modo vista previa son sintéticos: conciliarlos contra
    # el maestro real los dejaría a todos como "proveedor no dado de alta".
    if os.environ.get("AP_PREVIEW_MODE", "").strip() == "1":
        return
    master = load_provisioned_vendor_master()
    if master is None:
        return
    session.supplier_master = master
    session.supplier_master_summary = master.safe_summary()
    session.audit.add(
        agent="sistema",
        action="maestro-proveedores-sage-aplicado",
        result="ok",
        evidence={**master.safe_summary(), "origen": "provisionado"},
    )


def record_intake(session: TrialSession, canal: str, cantidad: int) -> None:
    """Registra una ingesta por canal (carga-manual | gmail) sin contenido."""
    session.audit.add(agent="sistema", action="ingesta", result=canal,
                      evidence={"canal": canal, "documentos": cantidad})


def _resolve_supplier(session: TrialSession, result):
    """Aplica el maestro activo y conserva solo el resultado no sensible."""
    if session.supplier_master is None:
        return None
    from ...app import match_supplier_to_sage
    from ...sage.vendor_master import (
        AMBIGUOUS_VENDOR_WARNING,
        FUZZY_VENDOR_FYI,
        IBAN_MISMATCH_WARNING,
        INACTIVE_VENDOR_WARNING,
        MISSING_VENDOR_IDENTITY_WARNING,
        TAX_ID_NOT_FOUND_WARNING,
        VENDOR_NOT_FOUND_WARNING,
    )

    sage_warnings = {
        AMBIGUOUS_VENDOR_WARNING,
        FUZZY_VENDOR_FYI,
        IBAN_MISMATCH_WARNING,
        INACTIVE_VENDOR_WARNING,
        MISSING_VENDOR_IDENTITY_WARNING,
        TAX_ID_NOT_FOUND_WARNING,
        VENDOR_NOT_FOUND_WARNING,
    }
    result.warnings = [
        warning for warning in (result.warnings or [])
        if str(warning) not in sage_warnings
    ]
    resolution = match_supplier_to_sage(result.document, session.supplier_master)
    if resolution.warning and resolution.warning not in result.warnings:
        result.warnings.append(resolution.warning)
    safe_resolution = resolution.safe_dict()
    if not resolution.accepted:
        previous_payment = session.approval_decisions.pop(str(result.doc_id), None)
        previous_review = session.review_decisions.get(str(result.doc_id)) or {}
        review_reverted = previous_review.get("status") in {
            "confirmed", "payment_exception_approved"
        }
        if review_reverted:
            session.review_decisions.pop(str(result.doc_id), None)
        safe_resolution["payment_decision_reverted"] = bool(previous_payment)
        safe_resolution["review_confirmation_reverted"] = review_reverted
    session.supplier_resolutions[str(result.doc_id)] = safe_resolution
    # La política de revisión lee el vínculo desde el propio resultado: así los
    # controles del maestro llegan a todas las vistas sin arrastrar la sesión
    # por cada firma (mismo criterio que source_text para el asistente).
    result.supplier_resolution = safe_resolution
    return resolution


def _audit_supplier_resolution(session: TrialSession, result, resolution) -> None:
    if resolution is None:
        return
    if resolution.status == "matched" and resolution.method == "fuzzy_name":
        action, audit_result = "proveedor-vinculado-por-similitud-nombre", "fyi"
    elif resolution.status == "matched":
        action, audit_result = "proveedor-vinculado-sage", resolution.method
    elif resolution.status == "ambiguous":
        action, audit_result = "proveedor-ambiguo-sage", "requiere-revision"
    else:
        action, audit_result = "proveedor-no-encontrado-sage", "requiere-revision"
    session.audit.add(
        agent="sistema",
        action=action,
        invoice_id=result.doc_id,
        result=audit_result,
        evidence={
            "metodo": resolution.method,
            "candidatos": resolution.candidate_count,
            "similitud": (
                f"{resolution.score:.4f}" if resolution.score is not None else None
            ),
            "tax_id_confirmado": resolution.tax_id_confirmed,
            "maestro": session.supplier_master_summary.get("fingerprint"),
            "decision_pago_previa_revertida": bool(
                session.supplier_resolutions.get(str(result.doc_id), {}).get(
                    "payment_decision_reverted")),
            "confirmacion_previa_revertida": bool(
                session.supplier_resolutions.get(str(result.doc_id), {}).get(
                    "review_confirmation_reverted")),
        },
    )


def load_sage_vendor_master(
    session: TrialSession, filename: str, content: bytes
) -> dict:
    """Valida el XLSX, lo mantiene en memoria y reconcilia la sesion completa."""
    from ...app import parse_sage_vendor_master

    master = parse_sage_vendor_master(content, filename)
    session.supplier_master = master
    session.supplier_master_summary = master.safe_summary()
    session.supplier_resolutions = {}
    session.audit.add(
        agent="sistema",
        action="maestro-proveedores-sage-cargado",
        result="ok",
        evidence=dict(session.supplier_master_summary),
    )
    for result in session.results:
        resolution = _resolve_supplier(session, result)
        _audit_supplier_resolution(session, result, resolution)
    return dict(session.supplier_master_summary)


def register_new_vendor(session: TrialSession, fila: dict, extras: dict) -> None:
    """Suma el alta al maestro vivo y la deja pendiente de envío a Sage.

    Incorporarla al maestro en memoria hace que las facturas de ese proveedor
    concilien desde el momento del alta, sin esperar al próximo export del ERP.
    """
    from ...sage.vendor_master import SageVendor, SageVendorMaster, _tax_keys

    master = session.supplier_master
    country = (fila.get("Sigla") or "").upper() or None
    vendor = SageVendor(
        source_id=fila.get("Código cuenta") or fila.get("CIF/DNI") or "",
        accounting_code=fila.get("Código cuenta") or None,
        legal_name=fila.get("Descripción") or "",
        trading_name=None,
        tax_id_keys=_tax_keys([fila.get("CIF/DNI")], country),
        country_code=country,
        iban=extras.get("iban") or None,
        bank_code=None,
        payment_terms_code=None,
        source_row=0,
        active=fila.get("Bloqueada") != "Sí",
    )
    if master is not None:
        session.supplier_master = SageVendorMaster(
            vendors=master.vendors + (vendor,),
            fingerprint=master.fingerprint,
            source_filename=master.source_filename,
            sheet_name=master.sheet_name,
            rows_seen=master.rows_seen + 1,
            rows_ignored=master.rows_ignored,
            inactive_count=master.inactive_count + int(not vendor.active),
            issues=master.issues,
        )
        session.supplier_master_summary = session.supplier_master.safe_summary()

    session.pending_vendors.append({**fila, "I.B.A.N.": extras.get("iban") or "",
                                    "BIC/SWIFT": extras.get("bic") or ""})
    session.audit.add(
        agent="alta-proveedor",
        action="alta-proveedor-registrada",
        result="pendiente-de-sage",
        evidence={
            "descripcion": fila.get("Descripción"),
            "sigla": country,
            "con_iban": bool(extras.get("iban")),
            "bloqueada": fila.get("Bloqueada") == "Sí",
            "escribe_en_sage": False,
        },
    )
    # Las facturas ya cargadas de ese proveedor pasan a conciliar.
    for result in session.results:
        _resolve_supplier(session, result)


def add_results(session: TrialSession, results) -> None:
    """Agrega resultados de extraccion y registra un evento de auditoria por doc
    (sin contenido del documento: solo metadatos)."""
    for r in results:
        if _already_present(session, r.doc_id):
            _record_duplicate_omitted(session, r.doc_id)
            continue
        session.results.append(r)
        supplier_resolution = _resolve_supplier(session, r)
        session.audit.add(
            agent="sistema",
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
        _audit_supplier_resolution(session, r, supplier_resolution)


def _already_present(session: TrialSession, doc_id: str,
                     file_hash: str | None = None) -> bool:
    if any(str(item.doc_id) == str(doc_id) for item in session.results):
        return True
    return bool(file_hash and file_hash in session.file_hashes.values())


def _record_duplicate_omitted(session: TrialSession, doc_id: str) -> None:
    session.audit.add(
        agent="sistema", action="documento-repetido-omitido", invoice_id=doc_id,
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
    supplier_resolution = _resolve_supplier(session, result)
    session.proc_seconds[result.doc_id] = round(max(0.0, float(seconds)), 3)
    session.processing_seconds += max(0.0, float(seconds))
    if file_hash:
        session.file_hashes[result.doc_id] = file_hash
    session.sources[result.doc_id] = source
    session.audit.add(
        agent="sistema",
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
    _audit_supplier_resolution(session, result, supplier_resolution)
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
            agent="sistema", action="sesion-deduplicada", result="reparada",
            evidence={"documentos_repetidos_eliminados": removed})
    return removed


def add_error(session: TrialSession, filename: str, detalle: str,
              seconds: float = 0.0) -> None:
    """Registra un documento que fallo el procesamiento (estado 'Error de
    procesamiento'). El detalle se trunca; no se guarda contenido del PDF."""
    session.errors.append((filename, detalle))
    session.processing_seconds += max(0.0, float(seconds))
    session.audit.add(
        agent="sistema",
        action="error-procesamiento",
        invoice_id=filename,
        result="error",
        evidence={"detalle": (detalle or "")[:160]},
    )


def add_processing_time(session: TrialSession, seconds: float) -> None:
    """Acumula el tiempo real de procesamiento medido dentro de esta sesion."""
    session.processing_seconds += max(0.0, float(seconds))


def record_event(session: TrialSession, action: str, evidence: dict | None = None) -> None:
    session.audit.add(agent="sistema", action=action, evidence=evidence or {})


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
    else:
        raise ValueError("Para un documento no fiscal, reclasificalo como factura o "
                         "autorizá la excepción para propuesta de pago.")
    result.document.update(clean)
    previous_payment = session.approval_decisions.pop(doc_id, None)
    decision = {"status": "confirmed", "actor": reviewer,
                "note": (note or "").strip()[:500], "fields_changed": changed,
                "timestamp": workflow.now_iso()}
    session.review_decisions[doc_id] = decision
    session.audit.add(
        agent=reviewer, action="revision-humana-confirmada", invoice_id=doc_id,
        result="confirmed", evidence={"campos_corregidos": changed,
                                      "motivo_informado": bool(decision["note"]),
                                      "decision_pago_previa_revertida": bool(previous_payment)},
    )
    return decision


def approve_payment_exception(session: TrialSession, doc_id: str, reviewer: str,
                              note: str) -> dict:
    """Autoriza humanamente que un documento no fiscal pase al gate de pago."""
    from . import workflow

    reviewer = (reviewer or "").strip()
    note = (note or "").strip()
    if not reviewer:
        raise ValueError("Ingresá el nombre de quien autoriza la excepción.")
    if not note:
        raise ValueError("Indicá el motivo de la autorización excepcional.")
    result = _result_by_id(session, doc_id)
    if result.document.get("document_type") == "invoice":
        raise ValueError("Las facturas fiscales deben confirmarse corrigiendo sus campos; "
                         "la excepción es solo para proformas, anticipos u otros documentos.")
    missing = workflow.missing_payment_fields(result.document)
    if missing:
        raise ValueError("Faltan datos mínimos para proponer el pago: " + ", ".join(missing))
    decision = {
        "status": "payment_exception_approved", "actor": reviewer,
        "note": note[:500], "fields_changed": [], "timestamp": workflow.now_iso(),
    }
    session.review_decisions[doc_id] = decision
    # Una autorización nueva revierte una exclusión/rechazo anterior del mismo
    # documento; la reversión queda visible en el audit trail.
    previous_payment = session.approval_decisions.pop(doc_id, None)
    session.audit.add(
        agent=reviewer, action="excepcion-pago-autorizada", invoice_id=doc_id,
        result="payment_exception_approved",
        evidence={"motivo_informado": True,
                  "decision_pago_previa_revertida": bool(previous_payment),
                  "requiere_aprobador_distinto": True,
                  "no_libera_dinero": True},
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
    if status in {"rejected", "excluded"} and not note:
        raise ValueError("La exclusión o el rechazo requieren un motivo.")

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
        supplier_master_summary=dict(stored.supplier_master_summary),
        supplier_resolutions=dict(stored.supplier_resolutions),
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
    page_keys = {"documents_list", "review_queue", "payment_documents"}
    return [
        key for key in all_keys
        if str(key) == _KEY
        or str(key).startswith(("_trial_", "_pilot_", "_close_session_"))
        or str(key) in page_keys
    ]


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
    if st.sidebar.button("Cerrar sesión de trabajo", width="stretch",
                         icon=":material/logout:",
                         key="_trial_clear_sidebar"):
        _clear_and_rerun()


def render_clear_action() -> None:
    """Accion visible de borrado para el area principal (p. ej. Resultados)."""
    import streamlit as st

    if st.button("Eliminar datos de esta sesión", width="stretch",
                 icon=":material/delete:",
                 key="_trial_clear_main"):
        _clear_and_rerun()
