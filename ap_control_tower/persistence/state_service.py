"""Servicio de transiciones de estado sobre documentos persistidos (Fase 2).

Aplica una transicion de fase del ciclo de vida a un ``Documento`` de la base:
valida contra la matriz del ciclo de vida (rechaza saltos inseguros) y anexa
un evento de auditoria ENCADENADO. Nunca actualiza ``estado_procesamiento``
(el estado operativo granular del motor); solo mueve ``fase_ciclo_vida``, que
es la vista canonica. La regla de negocio "no se libera sin aprobacion" la
garantiza la matriz (bloqueado/recibido -> liberado son saltos invalidos).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.lifecycle import (
    Phase,
    assert_transition,
    next_on_critical_data_change,
    phase_for_status,
)
from .models_sql import Documento
from .repositories import append_chained_event


class DocumentNotFound(RuntimeError):
    pass


def current_phase(doc: Documento) -> str:
    """Fase actual del documento: la explicita, o la derivada del estado, o RECIBIDO."""
    if doc.fase_ciclo_vida:
        return doc.fase_ciclo_vida
    derived = phase_for_status(doc.estado_procesamiento)
    return derived or Phase.RECIBIDO


def transition_document(
    session: Session, id_interno: str, destino: str, *,
    actor: str, accion: str = "transicion-estado", run_id: str,
    commit: str = "", evidencia: dict | None = None,
) -> str:
    """Transiciona ``id_interno`` a ``destino`` si es valido; audita el cambio.

    Levanta IllegalTransition (desde la matriz) ante un salto inseguro y
    DocumentNotFound si el documento no existe. Devuelve la fase destino.
    """
    doc = session.scalar(select(Documento).where(Documento.id_interno == id_interno))
    if doc is None:
        raise DocumentNotFound(f"documento inexistente: {id_interno}")
    origen = current_phase(doc)
    assert_transition(origen, destino)   # rechaza saltos inseguros

    doc.fase_ciclo_vida = destino
    session.flush()
    append_chained_event(
        session, run_id=run_id, actor=actor, accion=accion, commit=commit,
        invoice_id=id_interno, result=destino,
        estado_anterior=origen, estado_posterior=destino,
        evidencia=evidencia or {})
    return destino


def apply_critical_data_change(
    session: Session, id_interno: str, *, actor: str, run_id: str,
    commit: str = "", evidencia: dict | None = None,
) -> str:
    """Registra la edicion de un dato CRITICO: reinicia los controles.

    Si el pago ya se libero la matriz lo impide (IllegalTransition). Devuelve la
    fase resultante (normalmente ``controles_en_ejecucion``).
    """
    doc = session.scalar(select(Documento).where(Documento.id_interno == id_interno))
    if doc is None:
        raise DocumentNotFound(f"documento inexistente: {id_interno}")
    origen = current_phase(doc)
    destino = next_on_critical_data_change(origen)   # levanta si dinero liberado
    if destino != origen:
        assert_transition(origen, destino)
        doc.fase_ciclo_vida = destino
        session.flush()
    append_chained_event(
        session, run_id=run_id, actor=actor, accion="edicion-dato-critico",
        commit=commit, invoice_id=id_interno, result=destino,
        estado_anterior=origen, estado_posterior=destino,
        evidencia={**(evidencia or {}),
                   "nota": "editar datos criticos reinicia los controles"})
    return destino
