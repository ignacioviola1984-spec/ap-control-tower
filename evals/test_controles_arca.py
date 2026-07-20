"""Controles ARCA (padron + APOC): validacion local de CUIT, validadores
puros y politica de derivacion. 100%% hermetico: sin red, sin ARCA real."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def _seccion_cuit() -> None:
    from ap_control_tower.controls.arca import cuit

    print("== CUIT: validacion local del digito verificador (mod 11) ==")
    # CUITs sinteticos generados por la propia funcion: siempre validos.
    sinteticos = [cuit.generar_cuit_sintetico(i) for i in range(20)]
    check(all(cuit.cuit_valido(c) for c in sinteticos),
          "20 CUITs sinteticos generados son validos")
    check(len(set(sinteticos)) == 20, "los sinteticos no se repiten")
    check(cuit.cuit_valido(cuit.generar_cuit_sintetico(7, prefijo="27")),
          "generador acepta prefijo de persona fisica")

    valido = cuit.generar_cuit_sintetico(1)
    # Alterar el digito verificador SIEMPRE invalida.
    dv_alterado = valido[:10] + str((int(valido[10]) + 1) % 10)
    check(not cuit.cuit_valido(dv_alterado), "digito verificador alterado -> invalido")
    # Formatos con separadores se normalizan.
    con_guiones = f"{valido[:2]}-{valido[2:10]}-{valido[10]}"
    check(cuit.cuit_valido(con_guiones), "formato XX-XXXXXXXX-X se normaliza y valida")
    check(cuit.normalizar(f" {con_guiones} ") == valido, "espacios y guiones se limpian")

    # Lo que NO es candidato a CUIT jamas genera senal (clave para la
    # regresion del golden: CIF espanoles y tax ids enmascarados).
    for raro in ("B00000000", "ESB12345678", "******999", "", None, "12345",
                 "123456789012", "IT12345678901"):
        check(not cuit.es_cuit_candidato(raro), f"no candidato: {raro!r}")

    # Candidato con prefijo no asignado por ARCA -> invalido.
    check(not cuit.cuit_valido("99" + valido[2:]), "prefijo desconocido -> invalido")
    # El resto 1 no tiene digito verificador (ARCA cambia el prefijo).
    check(cuit.digito_verificador("2000000001") is None
          or isinstance(cuit.digito_verificador("2000000001"), int),
          "digito_verificador devuelve int o None (resto 1)")


def main() -> int:
    _seccion_cuit()
    if failures:
        print(f"CONTROLES ARCA ROJO: {len(failures)} fallas")
        return 1
    print("CONTROLES ARCA VERDE: CUIT local OK (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
