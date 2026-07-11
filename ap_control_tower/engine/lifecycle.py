"""Ciclo de vida explicito del documento AP (Fase 2).

Formaliza los estados del documento y sus transiciones VALIDAS. Es un modulo
PURO (solo-stdlib) y ADITIVO: el pipeline sigue emitiendo sus ``STATUS_*``
granulares sin cambios; esta capa provee (a) una maquina de estados canonica
para validar transiciones y rechazar saltos inseguros, y (b) un mapeo desde
los ``STATUS_*`` del motor a la fase canonica, para persistencia y UI.

Saltos inseguros que la matriz RECHAZA (probados en evals):
  - recibido -> liberado / preparado_para_pago  (no se paga sin procesar)
  - bloqueado -> liberado / preparado_para_pago  (no se libera un bloqueado)
  - requiere_revision -> aprobado                (no se aprueba sin la revision)
  - controles_en_ejecucion -> liberado           (debe pasar por aprobado)
  - liberado -> * (salvo cerrado)                (dinero ya salido)
  - editar un dato critico tras la aprobacion sin reiniciar los controles
"""

from __future__ import annotations

from ..models import (
    STATUS_ANTICIPO_EXCEPCION,
    STATUS_ANTICIPO_PENDIENTE,
    STATUS_ANTICIPO_RETENIDO,
    STATUS_BLOQUEADA,
    STATUS_CERRADA,
    STATUS_DOMICILIACION,
    STATUS_EN_LOTE,
    STATUS_LIBERADA_AL_BANCO,
    STATUS_LOTE_DEVUELTO,
    STATUS_OTRO_DOC,
    STATUS_PENDIENTE_DATOS_INTERNOS,
    STATUS_PROXIMO_CICLO,
    STATUS_RETENIDO_ALTA_PROVEEDOR,
    STATUS_TARJETA,
)


class Phase:
    """Estados canonicos del ciclo de vida de un documento (seccion 6)."""
    RECIBIDO = "recibido"
    VALIDANDO = "validando"
    EN_COLA = "en_cola"
    PROCESANDO = "procesando"
    EXTRAIDO = "extraido"
    CONTROLES_EN_EJECUCION = "controles_en_ejecucion"
    REQUIERE_REVISION = "requiere_revision"
    BLOQUEADO = "bloqueado"
    APROBADO = "aprobado"
    PREPARADO_PARA_PAGO = "preparado_para_pago"
    LIBERADO = "liberado"
    CERRADO = "cerrado"          # extension terminal: cierre contable de la demo
    FALLIDO = "fallido"
    EN_CUARENTENA = "en_cuarentena"


ALL_PHASES: tuple[str, ...] = (
    Phase.RECIBIDO, Phase.VALIDANDO, Phase.EN_COLA, Phase.PROCESANDO,
    Phase.EXTRAIDO, Phase.CONTROLES_EN_EJECUCION, Phase.REQUIERE_REVISION,
    Phase.BLOQUEADO, Phase.APROBADO, Phase.PREPARADO_PARA_PAGO, Phase.LIBERADO,
    Phase.CERRADO, Phase.FALLIDO, Phase.EN_CUARENTENA,
)

TERMINAL_PHASES: frozenset[str] = frozenset({Phase.CERRADO})

# Fases desde las que un dato critico YA NO puede editarse sin control humano de
# dinero (el pago esta liberado): editar es imposible, no "reinicia controles".
_MONEY_OUT_PHASES: frozenset[str] = frozenset({Phase.LIBERADO, Phase.CERRADO})

# Matriz de transiciones VALIDAS. Todo lo que no este aca es un salto invalido.
ALLOWED: dict[str, frozenset[str]] = {
    Phase.RECIBIDO: frozenset({Phase.VALIDANDO, Phase.EN_CUARENTENA, Phase.FALLIDO}),
    Phase.VALIDANDO: frozenset({Phase.EN_COLA, Phase.EN_CUARENTENA, Phase.FALLIDO}),
    Phase.EN_COLA: frozenset({Phase.PROCESANDO, Phase.FALLIDO}),
    Phase.PROCESANDO: frozenset({Phase.EXTRAIDO, Phase.EN_CUARENTENA, Phase.FALLIDO}),
    Phase.EXTRAIDO: frozenset({Phase.CONTROLES_EN_EJECUCION, Phase.FALLIDO}),
    Phase.CONTROLES_EN_EJECUCION: frozenset({
        Phase.REQUIERE_REVISION, Phase.BLOQUEADO, Phase.APROBADO, Phase.FALLIDO}),
    # el humano confirma datos -> se RE-EJECUTAN los controles (nunca directo a aprobado)
    Phase.REQUIERE_REVISION: frozenset({Phase.CONTROLES_EN_EJECUCION, Phase.BLOQUEADO}),
    # excepcion resuelta -> vuelve a controles
    Phase.BLOQUEADO: frozenset({Phase.CONTROLES_EN_EJECUCION}),
    # editar dato critico tras aprobar -> reinicia controles
    Phase.APROBADO: frozenset({Phase.PREPARADO_PARA_PAGO, Phase.CONTROLES_EN_EJECUCION}),
    # lote rechazado -> revision; edicion critica -> controles; falla tardia -> bloqueado
    Phase.PREPARADO_PARA_PAGO: frozenset({
        Phase.LIBERADO, Phase.REQUIERE_REVISION,
        Phase.CONTROLES_EN_EJECUCION, Phase.BLOQUEADO}),
    Phase.LIBERADO: frozenset({Phase.CERRADO}),
    Phase.CERRADO: frozenset(),
    Phase.FALLIDO: frozenset({Phase.EN_COLA}),        # reintento (Fase 5)
    Phase.EN_CUARENTENA: frozenset({Phase.VALIDANDO}),  # liberado tras revision manual
}


# Mapeo desde los STATUS_* granulares del motor a la fase canonica. No es
# bijectivo: varios estados operativos colapsan en una fase.
_STATUS_TO_PHASE: dict[str, str] = {
    STATUS_BLOQUEADA: Phase.BLOQUEADO,
    STATUS_ANTICIPO_EXCEPCION: Phase.BLOQUEADO,
    STATUS_EN_LOTE: Phase.PREPARADO_PARA_PAGO,
    STATUS_PROXIMO_CICLO: Phase.APROBADO,
    STATUS_PENDIENTE_DATOS_INTERNOS: Phase.REQUIERE_REVISION,
    STATUS_RETENIDO_ALTA_PROVEEDOR: Phase.REQUIERE_REVISION,
    STATUS_ANTICIPO_RETENIDO: Phase.REQUIERE_REVISION,
    STATUS_ANTICIPO_PENDIENTE: Phase.REQUIERE_REVISION,
    STATUS_OTRO_DOC: Phase.REQUIERE_REVISION,
    STATUS_LOTE_DEVUELTO: Phase.REQUIERE_REVISION,
    # metodos fuera del lote del jueves: controles OK, van a conciliacion propia
    STATUS_DOMICILIACION: Phase.APROBADO,
    STATUS_TARJETA: Phase.APROBADO,
    STATUS_LIBERADA_AL_BANCO: Phase.LIBERADO,
    STATUS_CERRADA: Phase.CERRADO,
}


class IllegalTransition(RuntimeError):
    """Transicion de estado invalida: intento de salto inseguro."""


def is_valid_phase(phase: str) -> bool:
    return phase in ALLOWED


def can_transition(origen: str, destino: str) -> bool:
    """True si ``origen -> destino`` es una transicion valida."""
    return destino in ALLOWED.get(origen, frozenset())


def assert_transition(origen: str, destino: str) -> None:
    """Valida una transicion; levanta IllegalTransition si es un salto inseguro."""
    if origen not in ALLOWED:
        raise IllegalTransition(f"estado de origen desconocido: {origen!r}")
    if destino not in ALLOWED:
        raise IllegalTransition(f"estado de destino desconocido: {destino!r}")
    if destino not in ALLOWED[origen]:
        raise IllegalTransition(
            f"transicion invalida {origen} -> {destino} "
            f"(validas desde {origen}: {sorted(ALLOWED[origen]) or 'ninguna (terminal)'})")


def phase_for_status(status: str | None) -> str | None:
    """Fase canonica para un STATUS_* del motor; None si no mapea."""
    if status is None:
        return None
    return _STATUS_TO_PHASE.get(status)


def next_on_critical_data_change(phase: str) -> str:
    """Fase resultante de editar un dato CRITICO.

    Regla: modificar datos criticos reinicia los controles. Si el pago ya se
    libero (liberado/cerrado) editar es imposible -> IllegalTransition.
    """
    if phase in _MONEY_OUT_PHASES:
        raise IllegalTransition(
            f"no se pueden modificar datos criticos en fase '{phase}': "
            f"el pago ya fue liberado")
    destino = Phase.CONTROLES_EN_EJECUCION
    # valido salvo que ya estemos antes de tener controles (aun no aplica)
    if phase in (Phase.CONTROLES_EN_EJECUCION, Phase.APROBADO,
                 Phase.PREPARADO_PARA_PAGO, Phase.REQUIERE_REVISION,
                 Phase.BLOQUEADO):
        return destino
    # en fases tempranas el cambio no obliga reinicio de controles (aun no hubo)
    return phase
