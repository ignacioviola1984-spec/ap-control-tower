"""Evals de la capa de persistencia (Fase 1). exit 0 = verde, != 0 = roto.

Aditivo y AISLADO de evals/run_evals.py: el contrato de la demo (19 grupos)
no cambia. Este grupo corre por separado y valida el round-trip motor->base.

Portable: por defecto usa SQLite en un archivo temporal (corre en cualquier
entorno, incluido CI sin Postgres). Si se define AP_TEST_DATABASE_URL apunta a
esa base (p. ej. el Postgres de docker-compose.dev.yml) y valida ahi tambien.

Si SQLAlchemy no esta instalado, hace SKIP con exit 0 (la dependencia de
persistencia es opcional; el motor y la demo no la necesitan).
"""

from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def _check_migrations() -> None:
    """Aplica las migraciones Alembic sobre base vacia y luego existente.

    Usa una base temporal propia (aislada del round-trip) apuntada por
    AP_DATABASE_URL, que es lo que resuelve migrations/env.py.
    """
    print("== Migraciones Alembic: base vacia y existente ==")
    try:
        from alembic import command
        from alembic.config import Config
    except Exception:
        check(True, "Alembic no instalado: migraciones SALTEADAS (opcional)")
        return
    import sqlite3

    mig_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    mig_tmp.close()
    prev = os.environ.get("AP_DATABASE_URL")
    os.environ["AP_DATABASE_URL"] = f"sqlite+pysqlite:///{mig_tmp.name}"
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(cfg, "head")                       # base vacia
        con = sqlite3.connect(mig_tmp.name)
        tablas = {r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")}
        con.close()
        check({"documentos", "facturas", "proveedores", "auditoria",
               "alembic_version"} <= tablas,
              f"upgrade head crea el esquema sobre base vacia ({len(tablas)} tablas)")
        command.upgrade(cfg, "head")                       # base existente
        check(True, "re-aplicar migraciones sobre base existente no falla ni borra")
    finally:
        if prev is None:
            os.environ.pop("AP_DATABASE_URL", None)
        else:
            os.environ["AP_DATABASE_URL"] = prev
        try:
            os.unlink(mig_tmp.name)
        except OSError:
            pass


def main() -> int:
    try:
        import sqlalchemy  # noqa: F401
    except Exception:
        print("== Persistencia: SALTEADO (SQLAlchemy no instalado) ==")
        print("  SKIP  instalar con: pip install -r requirements-persistence.txt")
        return 0

    from sqlalchemy import func, select

    from ap_control_tower.engine.pipeline import run_month
    from ap_control_tower.models import load_dataset
    from ap_control_tower.persistence import masking
    from ap_control_tower.persistence.models_sql import (
        AuditoriaEvento,
        Documento,
        Excepcion,
        Factura,
        LoteFactura,
        Proveedor,
    )
    from ap_control_tower.persistence.repositories import (
        persist_run,
        verify_persisted_chain,
    )
    from ap_control_tower.persistence.session import (
        build_engine,
        create_all,
        session_scope,
    )
    from ap_control_tower.persistence.config import DatabaseConfig

    dataset_path = ROOT / "data" / "synthetic_month.json"
    if not dataset_path.exists():
        print("FAIL  falta data/synthetic_month.json")
        return 1

    # -- migraciones Alembic: base vacia y luego existente (no destructivo)
    _check_migrations()

    url = os.environ.get("AP_TEST_DATABASE_URL")
    tmp = None
    if not url:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        url = f"sqlite+pysqlite:///{tmp.name}"
    print(f"== Persistencia contra motor {url.split(':')[0]} (round-trip) ==")

    engine = build_engine(DatabaseConfig(url=url))
    try:
        # -- migracion sobre base vacia: create_all no debe fallar ni borrar datos
        create_all(engine)
        create_all(engine)  # idempotente sobre base ya creada
        check(True, "esquema creado sobre base vacia y re-aplicado sin error")

        dataset = load_dataset(str(dataset_path))
        result, audit, ctx = run_month(dataset)

        with session_scope(engine) as s:
            summary = persist_run(s, dataset, result, audit)
        print(f"  resumen persistencia: {summary}")

        with session_scope(engine) as s:
            n_docs = s.scalar(select(func.count()).select_from(Documento))
            n_prov = s.scalar(select(func.count()).select_from(Proveedor))
            n_fac = s.scalar(select(func.count()).select_from(Factura))
            n_exc = s.scalar(select(func.count()).select_from(Excepcion))
            check(n_docs == len(dataset.invoices),
                  f"documentos persistidos: {n_docs} == {len(dataset.invoices)}")
            check(n_prov == len(dataset.vendors),
                  f"proveedores persistidos: {n_prov} == {len(dataset.vendors)}")
            check(summary["auditoria"] == len(audit.events),
                  f"eventos de auditoria persistidos: {summary['auditoria']}")

            # fraude INV-024: excepcion con fraud_alert, factura fuera de lote
            doc24 = s.scalar(select(Documento).where(Documento.id_interno == "INV-024"))
            check(doc24 is not None and doc24.estado_procesamiento == "bloqueada",
                  "INV-024 persistida como bloqueada")
            exc24 = s.scalar(select(Excepcion).where(
                Excepcion.documento_id == doc24.id, Excepcion.fraud_alert.is_(True)))
            check(exc24 is not None and exc24.control_id == "C6_DATOS_BANCARIOS",
                  "INV-024: excepcion de fraude C6 persistida con fraud_alert")
            fac24 = s.scalar(select(Factura).where(Factura.documento_id == doc24.id))
            in_lote = s.scalar(select(func.count()).select_from(LoteFactura)
                               .where(LoteFactura.factura_id == fac24.id))
            check(in_lote == 0, "INV-024 no esta en ningun lote (INVARIANTE-1 en base)")

            # enmascaramiento: el IBAN de la factura no viaja completo en la vista
            check(fac24.iban_en_factura and masking.mask_iban(fac24.iban_en_factura)
                  != fac24.iban_en_factura and "*" in masking.mask_iban(fac24.iban_en_factura),
                  "datos bancarios se enmascaran en la proyeccion de UI/logs")

            # integridad de la cadena de hash persistida
            check(verify_persisted_chain(s, result.run_id),
                  "cadena de hash de auditoria verifica desde la base")

        # -- idempotencia: re-persistir la MISMA corrida no duplica ni rompe
        with session_scope(engine) as s:
            summary2 = persist_run(s, dataset, result, audit)
        with session_scope(engine) as s:
            n_docs2 = s.scalar(select(func.count()).select_from(Documento))
            n_exc2 = s.scalar(select(func.count()).select_from(Excepcion))
            check(n_docs2 == n_docs and summary2["documentos"] == summary["documentos"],
                  "re-persistir la misma corrida es idempotente (documentos)")
            check(n_exc2 == n_exc,
                  "re-persistir no duplica excepciones (tablas de corrida reemplazadas)")

        # -- restriccion: factura fiscal duplicada ACTIVA es rechazada por la base
        from sqlalchemy.exc import IntegrityError
        dup_violada = False
        try:
            with session_scope(engine) as s:
                prov = s.scalar(select(Proveedor).limit(1))
                d1 = Documento(id_interno="DUP-A", tipo_documental="invoice",
                               estado_procesamiento="en_lote")
                d2 = Documento(id_interno="DUP-B", tipo_documental="invoice",
                               estado_procesamiento="en_lote")
                s.add_all([d1, d2]); s.flush()
                s.add(Factura(documento_id=d1.id, proveedor_id=prov.id,
                              numero_factura="F-DUP-1", importe_total=Decimal("100"),
                              estado_operativo="en_lote"))
                s.add(Factura(documento_id=d2.id, proveedor_id=prov.id,
                              numero_factura="F-DUP-1", importe_total=Decimal("100"),
                              estado_operativo="en_lote"))
                s.flush()
        except IntegrityError:
            dup_violada = True
        check(dup_violada,
              "factura fiscal duplicada ENTRE ACTIVAS -> IntegrityError (indice parcial)")

        # -- pero un duplicado BLOQUEADO (como INV-023) SI puede coexistir
        dup_bloqueado_ok = True
        try:
            with session_scope(engine) as s:
                prov = s.scalar(select(Proveedor).limit(1))
                d3 = Documento(id_interno="DUP-C", tipo_documental="invoice",
                               estado_procesamiento="bloqueada")
                s.add(d3); s.flush()
                s.add(Factura(documento_id=d3.id, proveedor_id=prov.id,
                              numero_factura="F-DUP-1", importe_total=Decimal("100"),
                              estado_operativo="bloqueada"))
                s.flush()
        except IntegrityError:
            dup_bloqueado_ok = False
        check(dup_bloqueado_ok,
              "un duplicado BLOQUEADO por C2 coexiste (no rompe el indice parcial)")

    finally:
        engine.dispose()
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    print()
    if failures:
        print(f"PERSISTENCIA ROJA: {len(failures)} fallas")
        return 1
    print("PERSISTENCIA VERDE: round-trip motor->base OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
