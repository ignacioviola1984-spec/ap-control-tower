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


def _fixture_apoc_text(cuits: list[str]) -> str:
    filas = "\n".join(f"{c},01/02/2020,15/02/2020,," for c in cuits)
    return ("# AFIP - Facturas Apocrifas\t\n"
            "# Generado - 20/7/2026\t\n"
            "# Estructura del Archivo: CUIT, Fecha Condicion Apocrifo, "
            "Fecha Publicacion, Descripcion \t\n"
            f"{filas}\n"
            "no-es-un-cuit,,,\n")


def _seccion_apoc() -> None:
    import io
    import zipfile
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from ap_control_tower.controls.arca import apoc_source, cuit
    from ap_control_tower.persistence.models_sql import (
        ArcaApocEntry, ArcaApocVersion, Base)

    print("== APOC: refresh versionado e idempotente, lookup local ==")
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    listados = [cuit.generar_cuit_sintetico(i) for i in range(5)]
    no_listado = cuit.generar_cuit_sintetico(99)
    texto = _fixture_apoc_text(listados)

    with Session(engine) as db:
        resumen = apoc_source.refresh_from_bytes(
            db, texto.encode(), origen="fixture-eval")
        db.commit()
        check(resumen["accion"] == "importada", "primera importacion crea version")
        check(resumen["cantidad_registros"] == 5, "5 CUITs importados")
        check(resumen["descartadas"] == 1, "la linea invalida se descarta y se cuenta")
        check(apoc_source.is_listed(db, listados[0]), "CUIT listado se encuentra")
        check(apoc_source.is_listed(db, f"{listados[0][:2]}-{listados[0][2:10]}-{listados[0][10]}"),
              "lookup normaliza guiones")
        check(not apoc_source.is_listed(db, no_listado), "CUIT no listado -> False")
        check(not apoc_source.is_listed(db, "B00000000"), "CIF europeo -> False")

        # Idempotencia: mismo contenido no crea version nueva.
        repetido = apoc_source.refresh_from_bytes(db, texto.encode(), origen="fixture-eval")
        check(repetido["accion"] == "sin_cambios", "mismo checksum -> sin_cambios")
        versiones = db.execute(select(ArcaApocVersion)).scalars().all()
        check(len(versiones) == 1, "sigue habiendo UNA version")

        # El ZIP oficial se acepta igual que el texto plano.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("FacturasApocrifas.txt", texto)
        zip_igual = apoc_source.refresh_from_bytes(db, buffer.getvalue(), origen="zip")
        check(zip_igual["accion"] == "sin_cambios", "zip con el mismo txt -> sin_cambios")

        # Contenido nuevo reemplaza el conjunto completo y versiona.
        texto2 = _fixture_apoc_text(listados[1:] + [no_listado])
        nuevo = apoc_source.refresh_from_bytes(db, texto2.encode(), origen="fixture-eval-2")
        db.commit()
        check(nuevo["accion"] == "importada", "checksum distinto -> nueva version")
        check(not apoc_source.is_listed(db, listados[0]), "CUIT retirado ya no figura")
        check(apoc_source.is_listed(db, no_listado), "CUIT agregado figura")
        entradas = db.execute(select(ArcaApocEntry)).scalars().all()
        check({e.version_id for e in entradas} == {nuevo["version_id"]},
              "todas las entradas apuntan a la version vigente")

        # Descarga vacia o corrupta NO pisa la base vigente.
        try:
            apoc_source.refresh_from_bytes(db, b"# solo comentarios\n", origen="x")
            check(False, "descarga vacia debe fallar")
        except ValueError:
            check(True, "descarga vacia rechazada; la base vigente se conserva")

        info = apoc_source.latest_version_info(db)
        check(info is not None and not info["desactualizada"],
              "base recien importada no esta desactualizada")
        # Antiguedad > 15 dias -> desactualizada (advertencia global).
        version = db.execute(select(ArcaApocVersion).order_by(
            ArcaApocVersion.id.desc())).scalars().first()
        version.fecha_descarga = datetime.now(timezone.utc) - timedelta(days=16)
        db.commit()
        info = apoc_source.latest_version_info(db)
        check(info["desactualizada"] and info["antiguedad_dias"] >= 16,
              "base con 16 dias -> desactualizada")

    check(apoc_source.parse_apoc_text("")[0] == [], "texto vacio -> sin entradas")


def main() -> int:
    _seccion_cuit()
    _seccion_apoc()
    if failures:
        print(f"CONTROLES ARCA ROJO: {len(failures)} fallas")
        return 1
    print("CONTROLES ARCA VERDE: CUIT local y APOC OK (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
