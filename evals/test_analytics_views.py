"""Evals del esquema `analytics` (vistas de solo lectura para BI externo).

exit 0 = verde, != 0 = contrato roto. Aditivo y AISLADO de evals/run_evals.py:
el contrato de la demo no cambia.

Requiere PostgreSQL: las vistas usan jsonb, regexp_replace y to_date, que SQLite
no tiene (la migracion 0006 es un no-op fuera de Postgres). Sin una URL de
Postgres configurada hace SKIP con exit 0, igual que evals/test_persistence.py
hace SKIP sin SQLAlchemy.

    docker compose -f docker-compose.dev.yml up -d postgres
    export AP_TEST_DATABASE_URL="postgresql+psycopg://ap:ap_dev_local@localhost:5432/ap_control_tower"
    python evals/test_analytics_views.py

Que prueba:
  1. Migracion: upgrade crea las 6 vistas con sus columnas; downgrade las quita
     y borra el esquema; re-upgrade vuelve a dejarlas (idempotente).
  2. Enmascarado EN LA VISTA: se insertan datos completos DIRECTO en las tablas
     base, salteando el enmascarado de la app, y se exige que el valor completo
     no aparezca en NINGUNA columna de NINGUNA vista.
  3. Minimizacion: los campos vetados (texto libre, identidad del cliente,
     nombres propios de revisores) no existen como columna en ninguna vista.
  4. Casts defensivos: basura del extractor sale como NULL, no rompe la vista.
  5. Permisos: con el rol de lectura, SELECT sobre tablas base falla y
     INSERT/UPDATE/DELETE fallan.

TODOS los datos son sinteticos e inventados: ningun dato real de ninguna
organizacion. El IBAN y los tax_id de abajo son ficticios (con checksum valido
para que sean realistas, pero no pertenecen a ninguna cuenta existente).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []

# ------------------------------------------------------------------ fixtures
# Datos INVENTADOS. El IBAN es el ejemplo canonico de la documentacion de
# validacion de IBAN; no corresponde a ninguna cuenta real.
IBAN_COMPLETO = "ES9121000418450200051332"
IBAN_ESPERADO = "****1332"
TAX_ID_COMPLETO = "B12345678"
TAX_ID_ESPERADO = "******678"
CLIENTE_TAX_ID_COMPLETO = "A87654321"
RUN_ID = "eval-analytics-views"

VISTAS = ("v_documents", "v_field_confidences", "v_review_queue",
          "v_payment_proposals", "v_run_metrics", "v_exceptions")

COLUMNAS_ESPERADAS: dict[str, set[str]] = {
    "v_documents": {
        "documento_id", "run_id", "archivo", "tipo_documental", "proveedor",
        "proveedor_razon_social", "proveedor_tax_id_enmascarado", "fecha_emision",
        "fecha_vencimiento", "moneda", "importe_neto", "importe_iva",
        "importe_total", "ruta_ap", "referencia_oc", "estado_circuito",
        "iban_ultimos4", "engine", "confianza_agregada", "paginas",
        "segundos_procesamiento", "procesado_en", "run_creado_en"},
    "v_field_confidences": {"run_id", "documento_id", "campo", "confianza"},
    "v_review_queue": {
        "documento_id", "run_id", "proveedor", "motivos", "motivos_cantidad",
        "estado_decision", "revisor_rol", "decidido_en", "campos_corregidos",
        "procesado_en"},
    "v_payment_proposals": {
        "documento_id", "lote_run_id", "proveedor", "proveedor_tax_id_enmascarado",
        "importe", "moneda", "fecha_vencimiento", "aprobado_en", "aprobador_rol",
        "iban_ultimos4", "run_creado_en"},
    "v_run_metrics": {
        "run_id", "origen", "creado_en", "actualizado_en", "documentos_procesados",
        "documentos_reconocidos", "documentos_sin_error", "errores",
        "derivados_a_revision", "pct_derivado_a_revision", "confianza_promedio",
        "segundos_procesamiento"},
    "v_exceptions": {
        "run_id", "documento_id", "tipo_advertencia", "severidad", "advertencia",
        "procesado_en"},
}

# Campos que NUNCA deben ser columna de una vista: texto libre del documento,
# identidad del cliente, y nombres propios de quien revisa o aprueba.
COLUMNAS_PROHIBIDAS = {
    "condiciones_pago", "fecha_vencimiento_texto",   # texto libre del documento
    "cliente_nombre", "cliente_tax_id",              # identidad de la organizacion
    "actor", "revisor", "aprobador", "nota", "note", # nombres propios / texto libre
    "iban", "proveedor_tax_id", "proveedor_cuenta_bancaria", "bic",  # sin enmascarar
}


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def _postgres_url() -> str | None:
    url = os.environ.get("AP_TEST_DATABASE_URL") or os.environ.get("AP_DATABASE_URL")
    if not url or not url.startswith(("postgresql", "postgres://")):
        return None
    return url


# ------------------------------------------------------------------ migracion
def _alembic_cfg():
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _run_alembic(url: str, revision: str) -> None:
    """Corre alembic con AP_DATABASE_URL apuntado a la base de test."""
    from alembic import command
    prev = os.environ.get("AP_DATABASE_URL")
    os.environ["AP_DATABASE_URL"] = url
    try:
        command.upgrade(_alembic_cfg(), revision) if revision != "-1" \
            else command.downgrade(_alembic_cfg(), "-1")
    finally:
        if prev is None:
            os.environ.pop("AP_DATABASE_URL", None)
        else:
            os.environ["AP_DATABASE_URL"] = prev


def _views_present(conn) -> set[str]:
    from sqlalchemy import text
    rows = conn.execute(text(
        "SELECT table_name FROM information_schema.views "
        "WHERE table_schema = 'analytics'")).scalars().all()
    return set(rows)


def _columns_of(conn, view: str) -> set[str]:
    from sqlalchemy import text
    return set(conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'analytics' AND table_name = :v"), {"v": view}
    ).scalars().all())


# ------------------------------------------------------------------ datos
def _seed(conn) -> None:
    """Inserta una corrida sintetica DIRECTO en las tablas base.

    Escribe los valores COMPLETOS a proposito (la app los enmascara al escribir;
    aca se saltea esa capa) para probar que el enmascarado de la vista no
    depende de la app. Incluye ademas basura del extractor para ejercitar las
    guardas de los casts.
    """
    from sqlalchemy import text

    _purge(conn)

    documento_ok = {
        "document_type": "invoice",
        "proveedor_nombre_comercial": "Suministros Ficticios",
        "proveedor_razon_social_legal": "Suministros Ficticios SL",
        "proveedor_tax_id": TAX_ID_COMPLETO,          # COMPLETO a proposito
        "cliente_nombre": "Organizacion Cliente",     # no debe salir en ninguna vista
        "cliente_tax_id": CLIENTE_TAX_ID_COMPLETO,    # no debe salir en ninguna vista
        "numero_factura": "FIC-2026-001",
        "fecha_emision": "2026-06-15",
        "fecha_vencimiento_texto": "45 days end of month",   # texto libre: fuera
        "fecha_vencimiento_calculada": "2026-08-14",
        "moneda": "EUR",
        "importe_neto": "1000.00",
        "importe_iva": "210.00",
        "importe_total": "1210.00",
        "tratamiento_iva": "nacional",
        "metodo_pago": "transferencia",
        "proveedor_banco": "Banco Ficticio",
        "proveedor_cuenta_bancaria": "21000418450200051332",
        "iban": IBAN_COMPLETO,                        # COMPLETO a proposito
        "iban_enmascarado": False,
        "bic": "FICTESMMXXX",
        "po_reference": "OC-2026-77",
        "project_reference": None,
        "condiciones_pago": "Pago a 45 dias fin de mes",     # texto libre: fuera
        "campos_ilegibles": [],
    }
    # Documento con basura del extractor: si un cast no tuviera guarda, la vista
    # entera reventaria al consultarse y se cortaria la sincronizacion del BI.
    documento_basura = dict(documento_ok)
    documento_basura.update({
        "numero_factura": "FIC-2026-002",
        "fecha_emision": "al inicio del estudio",   # _date_value deja el crudo
        "fecha_vencimiento_calculada": "2026-02-30",  # fecha inexistente
        "importe_total": "N/A",
        "importe_neto": None,
        "importe_iva": None,
        "po_reference": None,
        "iban": None,
        "proveedor_tax_id": None,
    })
    # Documento no reconocido.
    documento_otro = dict(documento_ok)
    documento_otro.update({"document_type": "other", "numero_factura": None,
                           "po_reference": None, "iban": None,
                           "proveedor_tax_id": None})

    conn.execute(text("""
        INSERT INTO trial_runs (run_id, source, document_count, error_count,
                                processing_seconds, metrics, errors,
                                review_decisions, approval_decisions)
        VALUES (:run_id, 'trial', 3, 0, 12.5, :metrics, '[]',
                :review, :approval)
    """), {
        "run_id": RUN_ID,
        "metrics": json.dumps({"documents": 3, "successful": 3, "errors": 0,
                               "invoices": 2, "with_warnings": 1,
                               "confidence": 0.83, "processing_seconds": 12.5}),
        "errors": "[]",
        # 'actor' y 'note' llevan datos personales / texto libre: estan en la
        # tabla base y NO deben aparecer en ninguna vista.
        "review": json.dumps({
            "doc-basura": {"status": "confirmed", "actor": "Nombre Apellido Inventado",
                           "note": "corregido a mano", "fields_changed": ["importe_total"],
                           "timestamp": "2026-07-15T10:30:00+00:00"}}),
        "approval": json.dumps({
            "doc-ok": {"status": "approved", "actor": "Otro Nombre Inventado",
                       "note": "ok para propuesta",
                       "timestamp": "2026-07-15T11:00:00+00:00"}}),
    })

    for doc_id, payload, warns, confidences in (
        ("doc-ok", documento_ok, [], {"numero_factura": "0.95", "importe_total": "0.91"}),
        ("doc-basura", documento_basura,
         ["baja confianza en: importe_total", "campos criticos ausentes: importe_total"],
         {"importe_total": "0.20"}),
        ("doc-otro", documento_otro, ["clasificada como other; revisar si es OC u otro soporte"],
         {}),
    ):
        conn.execute(text("""
            INSERT INTO trial_documents (run_id, doc_id, filename, file_hash, source,
                                         engine, pages, text_chars, confidence,
                                         warnings, document, field_confidences,
                                         processing_seconds)
            VALUES (:run_id, :doc_id, :filename, :file_hash, 'carga-manual',
                    'google_document_ai_invoice_parser', 1, 500, 0.83,
                    :warnings, :document, :field_confidences, 4.1)
        """), {
            "run_id": RUN_ID, "doc_id": doc_id, "filename": f"{doc_id}.pdf",
            "file_hash": secrets.token_hex(32),
            "warnings": json.dumps(warns), "document": json.dumps(payload),
            "field_confidences": json.dumps(confidences),
        })


def _purge(conn) -> None:
    from sqlalchemy import text
    conn.execute(text("DELETE FROM trial_documents WHERE run_id = :r"), {"r": RUN_ID})
    conn.execute(text("DELETE FROM trial_runs WHERE run_id = :r"), {"r": RUN_ID})


def _all_cells(conn, view: str) -> list[str]:
    """Todos los valores de todas las columnas de una vista, como texto."""
    from sqlalchemy import text
    rows = conn.execute(text(f"SELECT * FROM analytics.{view}")).mappings().all()
    return [str(value) for row in rows for value in row.values() if value is not None]


# ------------------------------------------------------------------ permisos
def _grant_sql(role: str) -> list[str]:
    """Politica de permisos, espejo de scripts/sql/create_zoho_ro_role.sql.

    El script es la fuente de verdad operativa (y trae su propia verificacion,
    seccion 4); aca se replica la politica porque el script usa metacomandos de
    psql (\\gexec, \\set) que no se pueden ejecutar por SQLAlchemy. Si se cambia
    la politica, hay que cambiar los dos.
    """
    return [
        f"REVOKE ALL ON SCHEMA public FROM {role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
        f"GRANT USAGE ON SCHEMA analytics TO {role}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {role}",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA analytics TO {role}",
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA analytics FROM {role}",
        f"REVOKE CREATE ON SCHEMA analytics FROM {role}",
    ]


def _drop_role(conn, role: str) -> None:
    """Elimina el rol de test de forma idempotente.

    DROP ROLE falla con DependentObjectsStillExist mientras el rol conserve
    privilegios (incluidos los EXECUTE sobre las funciones de analytics).
    DROP OWNED BY los revoca todos en la base actual; el rol no posee objetos
    propios porque nunca pudo crear ninguno.
    """
    conn.exec_driver_sql(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE 'DROP OWNED BY {role}';
                EXECUTE 'DROP ROLE {role}';
            END IF;
        END $$
    """)


def _check_permissions(engine, url: str) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    print("== Permisos del rol de solo lectura ==")
    role = "zoho_analytics_ro_eval"       # rol de test, no el productivo
    password = secrets.token_urlsafe(24)  # generada al vuelo, jamas en el repo

    # PostgreSQL no admite bind params en DDL: CREATE ROLE ... PASSWORD :pwd es
    # un error de sintaxis. La password se interpola, y token_urlsafe solo
    # produce [A-Za-z0-9_-], asi que no hay comilla que escapar. Se verifica
    # igual antes de construir el DDL.
    assert password.replace("-", "").replace("_", "").isalnum(), \
        "password de test con caracteres inesperados: no interpolar"

    try:
        with engine.begin() as conn:
            _drop_role(conn, role)
            conn.exec_driver_sql(
                f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER "
                f"NOCREATEDB NOCREATEROLE NOINHERIT")
            for stmt in _grant_sql(role):
                conn.exec_driver_sql(stmt)
    except Exception as exc:
        # Solo se saltea si el entorno realmente no deja crear roles. Cualquier
        # otro error es una falla: un SKIP silencioso aca esconderia justo la
        # verificacion mas importante del entregable.
        if "permission denied" in str(exc).lower() or "must be superuser" in str(exc).lower():
            check(True, "SKIP permisos: el usuario de test no puede crear roles")
            return
        check(False, f"no se pudo preparar el rol de lectura: {exc}")
        return

    ro_url = make_url(url).set(username=role, password=password)
    ro_engine = create_engine(ro_url)
    try:
        # SELECT sobre las vistas: PERMITIDO
        with ro_engine.connect() as conn:
            n = conn.execute(text("SELECT count(*) FROM analytics.v_documents")).scalar()
            check(n == 3, f"el rol lee analytics.v_documents ({n} filas)")

        # SELECT sobre tablas base: DENEGADO
        for tabla in ("trial_documents", "trial_runs"):
            denegado = False
            try:
                with ro_engine.connect() as conn:
                    conn.execute(text(f"SELECT 1 FROM public.{tabla} LIMIT 1"))
            except Exception as exc:
                denegado = "permission denied" in str(exc).lower()
            check(denegado, f"SELECT sobre la tabla base public.{tabla} denegado")

        # Escrituras: DENEGADAS (por permisos o por vista no actualizable;
        # ambas son "no escribe").
        for etiqueta, stmt in (
            ("INSERT", "INSERT INTO analytics.v_documents (documento_id) VALUES ('x')"),
            ("UPDATE", "UPDATE analytics.v_documents SET documento_id = 'x'"),
            ("DELETE", "DELETE FROM analytics.v_documents"),
        ):
            rechazado = False
            try:
                with ro_engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception as exc:
                mensaje = str(exc).lower()
                rechazado = ("permission denied" in mensaje
                             or "cannot insert into view" in mensaje
                             or "cannot update view" in mensaje
                             or "cannot delete from view" in mensaje)
            check(rechazado, f"{etiqueta} sobre analytics.v_documents rechazado")

        # Crear objetos: DENEGADO
        denegado = False
        try:
            with ro_engine.begin() as conn:
                conn.execute(text("CREATE TABLE analytics.probe (x int)"))
        except Exception as exc:
            denegado = "permission denied" in str(exc).lower()
        check(denegado, "CREATE TABLE en analytics denegado")
    finally:
        ro_engine.dispose()
        with engine.begin() as conn:
            _drop_role(conn, role)


# ------------------------------------------------------------------ main
def main() -> int:
    try:
        import sqlalchemy  # noqa: F401
        import alembic     # noqa: F401
    except Exception:
        print("== Vistas analytics: SALTEADO (SQLAlchemy/Alembic no instalados) ==")
        print("  SKIP  instalar con: pip install -r requirements-persistence.txt")
        return 0

    url = _postgres_url()
    if not url:
        print("== Vistas analytics: SALTEADO (requiere PostgreSQL) ==")
        print("  SKIP  docker compose -f docker-compose.dev.yml up -d postgres")
        print("  SKIP  export AP_TEST_DATABASE_URL=postgresql+psycopg://...")
        return 0

    from sqlalchemy import create_engine, text

    print(f"== Vistas analytics contra {url.split('://')[0]} ==")
    engine = create_engine(url)
    try:
        # -- 1. migracion
        print("== Migracion 0006: upgrade / downgrade / re-upgrade ==")
        _run_alembic(url, "head")
        with engine.connect() as conn:
            presentes = _views_present(conn)
        check(set(VISTAS) <= presentes,
              f"upgrade head crea las {len(VISTAS)} vistas de analytics ({len(presentes)} presentes)")

        for view, esperadas in COLUMNAS_ESPERADAS.items():
            with engine.connect() as conn:
                cols = _columns_of(conn, view)
            check(cols == esperadas,
                  f"{view}: columnas exactas ({len(cols)})"
                  + (f" -- difieren: {cols ^ esperadas}" if cols != esperadas else ""))

        _run_alembic(url, "-1")
        with engine.connect() as conn:
            existe = conn.execute(text(
                "SELECT count(*) FROM information_schema.schemata "
                "WHERE schema_name = 'analytics'")).scalar()
        check(existe == 0, "downgrade -1 borra las vistas y el esquema analytics")

        _run_alembic(url, "head")
        with engine.connect() as conn:
            check(set(VISTAS) <= _views_present(conn),
                  "re-upgrade vuelve a crear las vistas (migracion re-aplicable)")

        # -- 2. minimizacion: campos vetados no existen como columna
        print("== Minimizacion: campos vetados fuera de toda vista ==")
        for view in VISTAS:
            with engine.connect() as conn:
                cols = _columns_of(conn, view)
            filtradas = cols & COLUMNAS_PROHIBIDAS
            check(not filtradas, f"{view}: sin columnas vetadas"
                  + (f" -- expone {filtradas}" if filtradas else ""))

        # -- 3. datos sinteticos + enmascarado
        print("== Enmascarado en la vista (datos completos en la tabla base) ==")
        with engine.begin() as conn:
            _seed(conn)

        with engine.connect() as conn:
            fila = conn.execute(text(
                "SELECT iban_ultimos4, proveedor_tax_id_enmascarado, importe_total, "
                "       fecha_emision, ruta_ap, estado_circuito, referencia_oc "
                "FROM analytics.v_documents WHERE documento_id = 'doc-ok'")).mappings().one()
        check(fila["iban_ultimos4"] == IBAN_ESPERADO,
              f"IBAN completo -> '{IBAN_ESPERADO}' en la vista (dio '{fila['iban_ultimos4']}')")
        check(fila["proveedor_tax_id_enmascarado"] == TAX_ID_ESPERADO,
              f"tax_id completo -> '{TAX_ID_ESPERADO}' (dio '{fila['proveedor_tax_id_enmascarado']}')")
        check(fila["ruta_ap"] == "po", f"ruta_ap derivada de po_reference: {fila['ruta_ap']}")
        check(str(fila["importe_total"]) == "1210.00",
              f"importe_total numerico: {fila['importe_total']}")
        check(str(fila["fecha_emision"]) == "2026-06-15",
              f"fecha_emision tipada: {fila['fecha_emision']}")

        # el valor COMPLETO no puede aparecer en ninguna columna de ninguna vista
        for view in VISTAS:
            with engine.connect() as conn:
                celdas = " || ".join(_all_cells(conn, view))
            for etiqueta, secreto in (("IBAN", IBAN_COMPLETO),
                                      ("tax_id proveedor", TAX_ID_COMPLETO),
                                      ("tax_id cliente", CLIENTE_TAX_ID_COMPLETO),
                                      ("cuenta bancaria", "21000418450200051332")):
                check(secreto not in celdas,
                      f"{view}: el {etiqueta} completo NO aparece en ninguna columna")

        # nombres propios y texto libre tampoco viajan como VALOR
        for view in VISTAS:
            with engine.connect() as conn:
                celdas = " || ".join(_all_cells(conn, view))
            for etiqueta, valor in (("nombre del revisor", "Nombre Apellido Inventado"),
                                    ("nombre del aprobador", "Otro Nombre Inventado"),
                                    ("nota humana", "corregido a mano"),
                                    ("texto libre del documento", "Pago a 45 dias fin de mes"),
                                    ("nombre del cliente", "Organizacion Cliente")):
                check(valor not in celdas, f"{view}: no expone {etiqueta}")

        # -- 4. casts defensivos
        print("== Casts defensivos: basura del extractor -> NULL, no error ==")
        with engine.connect() as conn:
            basura = conn.execute(text(
                "SELECT fecha_emision, fecha_vencimiento, importe_total, estado_circuito "
                "FROM analytics.v_documents WHERE documento_id = 'doc-basura'")).mappings().one()
        check(basura["fecha_emision"] is None,
              "fecha_emision 'al inicio del estudio' -> NULL (no rompe la vista)")
        check(basura["fecha_vencimiento"] is None,
              "fecha inexistente '2026-02-30' -> NULL (to_date lanza DatetimeFieldOverflow "
              "en PG16: sin la funcion tolerante, reventaria la vista entera)")
        check(basura["importe_total"] is None, "importe_total 'N/A' -> NULL")

        with engine.connect() as conn:
            otro = conn.execute(text(
                "SELECT estado_circuito FROM analytics.v_documents "
                "WHERE documento_id = 'doc-otro'")).scalar()
        check(otro == "no_reconocido", f"document_type 'other' -> no_reconocido (dio {otro})")

        # -- 5. semantica de las vistas restantes
        print("== Semantica de las vistas ==")
        with engine.connect() as conn:
            n_conf = conn.execute(text(
                "SELECT count(*) FROM analytics.v_field_confidences "
                "WHERE run_id = :r"), {"r": RUN_ID}).scalar()
            check(n_conf == 3, f"v_field_confidences en formato largo: {n_conf} filas (esperadas 3)")

            rol = conn.execute(text(
                "SELECT revisor_rol FROM analytics.v_review_queue "
                "WHERE documento_id = 'doc-basura'")).scalar()
            check(rol == "revisor_humano", f"v_review_queue expone ROL, no nombre: {rol}")

            motivos = conn.execute(text(
                "SELECT motivos FROM analytics.v_review_queue "
                "WHERE documento_id = 'doc-basura'")).scalar()
            check(motivos is not None and "baja confianza" in motivos,
                  f"v_review_queue trae los motivos del extractor: {motivos!r}")

            prop = conn.execute(text(
                "SELECT documento_id, aprobador_rol, importe FROM analytics.v_payment_proposals "
                "WHERE lote_run_id = :r"), {"r": RUN_ID}).mappings().all()
            check(len(prop) == 1 and prop[0]["documento_id"] == "doc-ok",
                  f"v_payment_proposals: solo las aprobadas ({len(prop)} fila/s)")
            check(prop and prop[0]["aprobador_rol"] == "aprobador_pagos",
                  "v_payment_proposals expone ROL del aprobador, no su nombre")

            met = conn.execute(text(
                "SELECT documentos_procesados, derivados_a_revision, pct_derivado_a_revision, "
                "       confianza_promedio FROM analytics.v_run_metrics "
                "WHERE run_id = :r"), {"r": RUN_ID}).mappings().one()
            check(met["documentos_procesados"] == 3,
                  f"v_run_metrics documentos: {met['documentos_procesados']}")
            check(str(met["pct_derivado_a_revision"]) == "33.33",
                  f"v_run_metrics % derivado calculado: {met['pct_derivado_a_revision']}")

            exc = conn.execute(text(
                "SELECT tipo_advertencia, severidad FROM analytics.v_exceptions "
                "WHERE documento_id = 'doc-basura' ORDER BY tipo_advertencia")).mappings().all()
            tipos = {r["tipo_advertencia"] for r in exc}
            check(tipos == {"baja_confianza", "campos_criticos_ausentes"},
                  f"v_exceptions normaliza el tipo de advertencia: {tipos}")

        # -- 6. permisos
        _check_permissions(engine, url)

    finally:
        try:
            with engine.begin() as conn:
                _purge(conn)
        except Exception:
            pass
        engine.dispose()

    print()
    if failures:
        print(f"VISTAS ANALYTICS ROJAS: {len(failures)} fallas")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("VISTAS ANALYTICS VERDES: migracion, enmascarado y permisos OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
