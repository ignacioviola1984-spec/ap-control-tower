"""Evals del ciclo de vida del documento (Fase 2). exit 0 = verde.

Puro: no necesita base ni SQLAlchemy. Prueba la matriz de transiciones, los
saltos inseguros que deben rechazarse, el mapeo STATUS_* -> fase y la regla de
edicion de datos criticos.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower.engine import lifecycle as lc                 # noqa: E402
from ap_control_tower.engine.lifecycle import IllegalTransition, Phase  # noqa: E402
from ap_control_tower import models as dom                          # noqa: E402

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def expect_illegal(origen: str, destino: str) -> None:
    try:
        lc.assert_transition(origen, destino)
    except IllegalTransition:
        check(True, f"{origen} -> {destino} rechazado (salto inseguro)")
    else:
        check(False, f"{origen} -> {destino} NO fue rechazado")


def main() -> int:
    print("== 1. Cobertura de estados y matriz ==")
    check(len(lc.ALL_PHASES) == 14, f"14 fases canonicas ({len(lc.ALL_PHASES)})")
    check(all(p in lc.ALLOWED for p in lc.ALL_PHASES),
          "toda fase tiene entrada en la matriz de transiciones")
    check(all(d in lc.ALLOWED for s in lc.ALLOWED.values() for d in s),
          "todo destino de la matriz es una fase valida (sin destinos colgados)")
    check(lc.ALLOWED[Phase.CERRADO] == frozenset(), "cerrado es terminal")

    print("== 2. Camino feliz completo es valido ==")
    happy = [Phase.RECIBIDO, Phase.VALIDANDO, Phase.EN_COLA, Phase.PROCESANDO,
             Phase.EXTRAIDO, Phase.CONTROLES_EN_EJECUCION, Phase.APROBADO,
             Phase.PREPARADO_PARA_PAGO, Phase.LIBERADO, Phase.CERRADO]
    ok = True
    for a, b in zip(happy, happy[1:]):
        try:
            lc.assert_transition(a, b)
        except IllegalTransition:
            ok = False
    check(ok, "recibido -> ... -> liberado -> cerrado es un camino valido")

    print("== 3. Saltos inseguros rechazados (seccion 6) ==")
    expect_illegal(Phase.RECIBIDO, Phase.LIBERADO)
    expect_illegal(Phase.RECIBIDO, Phase.PREPARADO_PARA_PAGO)
    expect_illegal(Phase.BLOQUEADO, Phase.LIBERADO)
    expect_illegal(Phase.BLOQUEADO, Phase.PREPARADO_PARA_PAGO)
    expect_illegal(Phase.REQUIERE_REVISION, Phase.APROBADO)
    expect_illegal(Phase.REQUIERE_REVISION, Phase.PREPARADO_PARA_PAGO)
    expect_illegal(Phase.CONTROLES_EN_EJECUCION, Phase.LIBERADO)
    expect_illegal(Phase.LIBERADO, Phase.PREPARADO_PARA_PAGO)
    expect_illegal(Phase.LIBERADO, Phase.APROBADO)

    print("== 4. Revision humana obliga a re-ejecutar controles ==")
    check(lc.can_transition(Phase.REQUIERE_REVISION, Phase.CONTROLES_EN_EJECUCION),
          "requiere_revision -> controles_en_ejecucion es valido")
    check(not lc.can_transition(Phase.REQUIERE_REVISION, Phase.APROBADO),
          "requiere_revision -> aprobado NO es valido (no se aprueba sin revision)")

    print("== 5. Editar dato critico reinicia los controles ==")
    check(lc.next_on_critical_data_change(Phase.APROBADO) == Phase.CONTROLES_EN_EJECUCION,
          "editar tras aprobado -> vuelve a controles_en_ejecucion")
    check(lc.next_on_critical_data_change(Phase.PREPARADO_PARA_PAGO)
          == Phase.CONTROLES_EN_EJECUCION,
          "editar un preparado_para_pago -> vuelve a controles")
    for terminal in (Phase.LIBERADO, Phase.CERRADO):
        try:
            lc.next_on_critical_data_change(terminal)
        except IllegalTransition:
            check(True, f"editar dato critico en '{terminal}' es imposible (dinero liberado)")
        else:
            check(False, f"editar en '{terminal}' deberia ser imposible")

    print("== 6. Mapeo STATUS_* del motor -> fase canonica ==")
    check(lc.phase_for_status(dom.STATUS_BLOQUEADA) == Phase.BLOQUEADO,
          "bloqueada -> bloqueado")
    check(lc.phase_for_status(dom.STATUS_EN_LOTE) == Phase.PREPARADO_PARA_PAGO,
          "en_lote -> preparado_para_pago")
    check(lc.phase_for_status(dom.STATUS_LIBERADA_AL_BANCO) == Phase.LIBERADO,
          "liberada_al_banco -> liberado")
    check(lc.phase_for_status(dom.STATUS_CERRADA) == Phase.CERRADO,
          "cerrada -> cerrado")
    check(lc.phase_for_status(dom.STATUS_PENDIENTE_DATOS_INTERNOS) == Phase.REQUIERE_REVISION,
          "pendiente_datos_internos -> requiere_revision")
    check(lc.phase_for_status("estado_inexistente") is None,
          "estado desconocido -> None (no inventa fase)")

    # todo estado que el pipeline puede emitir para una factura tiene fase
    emitidos = {
        dom.STATUS_BLOQUEADA, dom.STATUS_EN_LOTE, dom.STATUS_PROXIMO_CICLO,
        dom.STATUS_PENDIENTE_DATOS_INTERNOS, dom.STATUS_RETENIDO_ALTA_PROVEEDOR,
        dom.STATUS_ANTICIPO_RETENIDO, dom.STATUS_ANTICIPO_PENDIENTE,
        dom.STATUS_ANTICIPO_EXCEPCION, dom.STATUS_DOMICILIACION, dom.STATUS_TARJETA,
        dom.STATUS_OTRO_DOC,
    }
    sin_fase = [s for s in emitidos if lc.phase_for_status(s) is None]
    check(not sin_fase, f"todo estado operativo emitido mapea a una fase ({sin_fase or 'ok'})")

    print()
    if failures:
        print(f"CICLO DE VIDA ROJO: {len(failures)} fallas")
        return 1
    print("CICLO DE VIDA VERDE: matriz de transiciones y mapeo OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
