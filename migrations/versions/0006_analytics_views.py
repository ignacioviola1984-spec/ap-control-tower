"""Esquema analytics: vistas curadas de solo lectura para BI externo.

Revision ID: 0006_analytics_views
Revises: 0005_trial_workflow_decisions
Create Date: 2026-07-16

Expone al BI externo un esquema ``analytics`` con vistas curadas sobre las
tablas del Trial (``trial_runs`` / ``trial_documents``). Nada del
comportamiento existente cambia: esto solo AGREGA vistas.

Criterios que hace cumplir esta migracion:

* **Minimizacion**: columnas explicitas, nunca ``SELECT *``. Fuera del alcance
  quedan el texto libre del documento (``condiciones_pago``,
  ``fecha_vencimiento_texto``), la identidad del cliente (``cliente_nombre``,
  ``cliente_tax_id``), las notas escritas por humanos y los nombres propios de
  revisores/aprobadores. Ante la duda, el campo no se expone.
* **Enmascarado en la vista, no en la app**: el IBAN sale como ``****`` + los 4
  ultimos; el tax_id replica el criterio de ``persistence/masking.py``. La capa
  de aplicacion ya enmascara al escribir, pero la vista no confia en eso: si un
  dia se escribiera un valor completo, la vista lo sigue enmascarando. Ambas
  expresiones son idempotentes sobre un valor ya enmascarado.
* **Casts defensivos**: ``document`` es JSON producido por el extractor y sus
  valores son texto arbitrario (``extraction/document_ai.py::_date_value``
  devuelve el crudo si no matchea ISO). Un cast directo a ``date``/``numeric``
  reventaria la vista entera -- y con ella la sincronizacion del BI -- por un
  solo documento con basura. Cada cast va con guarda: lo que no es un valor
  valido se expone como NULL, no como error.

Postgres-only: en SQLite (usado por evals/test_persistence.py) es un no-op. Las
vistas usan jsonb, regexp_replace y to_date, que SQLite no tiene; saltearla
mantiene verde el contrato de tests existente sin condicionar la demo.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006_analytics_views"
down_revision: Union[str, None] = "0005_trial_workflow_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "analytics"

VIEWS: tuple[str, ...] = (
    "v_documents",
    "v_field_confidences",
    "v_review_queue",
    "v_payment_proposals",
    "v_run_metrics",
    "v_exceptions",
)


# --------------------------------------------------------------- expresiones
# Helpers que construyen las expresiones SQL. Son deliberadamente chicos y con
# un solo proposito: la regla de exposicion de cada campo se lee de un vistazo
# y no se repite (ni se desincroniza) entre vistas.

def _txt(field: str, src: str = "b.doc") -> str:
    """Campo de texto del JSON del documento; '' se normaliza a NULL."""
    return f"NULLIF(btrim({src} ->> '{field}'), '')"


def _num(field: str, src: str = "b.doc") -> str:
    """Numero del JSON via ``analytics.numero_o_null`` (ver FUNCIONES)."""
    return f"{SCHEMA}.numero_o_null({src} ->> '{field}')"


def _date(field: str, src: str = "b.doc") -> str:
    """Fecha del JSON via ``analytics.fecha_iso_o_null`` (ver FUNCIONES)."""
    return f"{SCHEMA}.fecha_iso_o_null({src} ->> '{field}')"


def _iban_last4(field: str = "iban", src: str = "b.doc") -> str:
    """IBAN -> '****' + 4 ultimos.

    Mas estricto que ``masking.mask_iban`` (que deja ver pais + 2 de control):
    hacia el BI externo no viaja la cabecera. Idempotente: aplicado sobre un
    valor ya enmascarado ('ES91********1332') da el mismo '****1332'.
    """
    expr = _txt(field, src)
    compact = f"regexp_replace({expr}, '\\s', '', 'g')"
    return (
        f"CASE WHEN {expr} IS NULL THEN NULL "
        f"WHEN length({compact}) <= 4 THEN repeat('*', length({compact})) "
        f"ELSE '****' || right({compact}, 4) END"
    )


def _tax_id_masked(field: str, src: str = "b.doc") -> str:
    """tax_id -> replica exacta de ``masking.mask_tax_id`` (3 ultimos visibles)."""
    expr = _txt(field, src)
    return (
        f"CASE WHEN {expr} IS NULL THEN NULL "
        f"WHEN length({expr}) <= 3 THEN repeat('*', length({expr})) "
        f"ELSE repeat('*', length({expr}) - 3) || right({expr}, 3) END"
    )


def _decision(src: str, key: str) -> str:
    """Campo de la decision humana del documento, si la decision es un objeto."""
    return (
        f"CASE WHEN jsonb_typeof({src} -> b.doc_id) = 'object' "
        f"THEN {src} -> b.doc_id ->> '{key}' END"
    )


def _ts(expr: str) -> str:
    """Timestamp de una decision via ``analytics.ts_o_null`` (ver FUNCIONES)."""
    return f"{SCHEMA}.ts_o_null({expr})"


# ---------------------------------------------------------------- FUNCIONES
# Conversores tolerantes. Existen porque el JSON del extractor es texto
# arbitrario y un cast que explota no devuelve una fila mala: rompe la vista
# ENTERA, y con ella la sincronizacion del BI. Verificado contra PostgreSQL 16:
# to_date('2026-02-30','YYYY-MM-DD') NO redondea, lanza DatetimeFieldOverflow.
# Por eso no alcanza con una regex + round-trip: hace falta atrapar la
# excepcion. Cada funcion devuelve NULL ante cualquier entrada invalida.
#
# STRICT: NULL de entrada -> NULL sin ejecutar el cuerpo.
# IMMUTABLE donde el cast lo es (to_date y ::numeric lo son); ts_o_null queda
# STABLE porque ::timestamptz depende del GUC TimeZone.
_FUNCIONES: tuple[str, ...] = (
    f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.fecha_iso_o_null(txt text)
RETURNS date LANGUAGE plpgsql IMMUTABLE STRICT AS $fn$
DECLARE
    iso text := left(btrim(txt), 10);
BEGIN
    IF iso !~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' THEN
        RETURN NULL;          -- 'al inicio del estudio', '', texto crudo
    END IF;
    RETURN to_date(iso, 'YYYY-MM-DD');
EXCEPTION WHEN others THEN
    RETURN NULL;              -- '2026-02-30', '2026-13-01': fechas inexistentes
END
$fn$
""",
    f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.numero_o_null(txt text)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE STRICT AS $fn$
BEGIN
    IF btrim(txt) !~ '^-?[0-9]+(\\.[0-9]+)?$' THEN
        RETURN NULL;          -- 'N/A', '', texto
    END IF;
    RETURN btrim(txt)::numeric;
EXCEPTION WHEN others THEN
    RETURN NULL;              -- desbordes absurdos
END
$fn$
""",
    f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.ts_o_null(txt text)
RETURNS timestamptz LANGUAGE plpgsql STABLE STRICT AS $fn$
BEGIN
    RETURN btrim(txt)::timestamptz;
EXCEPTION WHEN others THEN
    RETURN NULL;
END
$fn$
""",
)

FUNCIONES_NOMBRES: tuple[str, ...] = (
    "fecha_iso_o_null(text)",
    "numero_o_null(text)",
    "ts_o_null(text)",
)

_N_WARNINGS = "CASE WHEN jsonb_typeof(b.warns) = 'array' THEN jsonb_array_length(b.warns) ELSE 0 END"

_REVIEW_STATUS = _decision("b.rev", "status")
_APPROVAL_STATUS = _decision("b.apr", "status")

# Fuente comun: un documento del Trial junto a las decisiones humanas de su
# corrida. COALESCE + cast a jsonb para no depender de si la columna quedo
# declarada json o jsonb, y para tolerar NULL.
_BASE_CTE = """
    WITH b AS (
        SELECT
            td.run_id                                            AS run_id,
            td.doc_id                                            AS doc_id,
            td.filename                                          AS filename,
            td.engine                                            AS engine,
            td.pages                                             AS pages,
            td.confidence                                        AS confidence,
            td.processing_seconds                                AS processing_seconds,
            td.created_at                                        AS created_at,
            COALESCE(td.document::jsonb, '{}'::jsonb)            AS doc,
            COALESCE(td.warnings::jsonb, '[]'::jsonb)            AS warns,
            COALESCE(tr.review_decisions::jsonb, '{}'::jsonb)    AS rev,
            COALESCE(tr.approval_decisions::jsonb, '{}'::jsonb)  AS apr,
            tr.created_at                                        AS run_created_at
        FROM public.trial_documents td
        JOIN public.trial_runs tr ON tr.run_id = td.run_id
    )
"""


def _v_documents() -> str:
    return f"""
CREATE VIEW {SCHEMA}.v_documents AS
{_BASE_CTE}
    SELECT
        b.doc_id                                  AS documento_id,
        b.run_id                                  AS run_id,
        b.filename                                AS archivo,
        {_txt('document_type')}                   AS tipo_documental,
        COALESCE({_txt('proveedor_razon_social_legal')},
                 {_txt('proveedor_nombre_comercial')})
                                                  AS proveedor,
        {_txt('proveedor_razon_social_legal')}    AS proveedor_razon_social,
        {_tax_id_masked('proveedor_tax_id')}      AS proveedor_tax_id_enmascarado,
        {_date('fecha_emision')}                  AS fecha_emision,
        {_date('fecha_vencimiento_calculada')}    AS fecha_vencimiento,
        upper({_txt('moneda')})                   AS moneda,
        {_num('importe_neto')}                    AS importe_neto,
        {_num('importe_iva')}                     AS importe_iva,
        {_num('importe_total')}                   AS importe_total,
        CASE
            WHEN {_txt('document_type')} = 'proforma_or_advance_request' THEN 'anticipo'
            WHEN {_txt('document_type')} = 'other'                       THEN 'otro'
            WHEN {_txt('po_reference')} IS NOT NULL                      THEN 'po'
            ELSE 'non_po'
        END                                       AS ruta_ap,
        {_txt('po_reference')}                    AS referencia_oc,
        CASE
            WHEN COALESCE({_txt('document_type')}, '')
                 NOT IN ('invoice', 'proforma_or_advance_request')  THEN 'no_reconocido'
            WHEN {_REVIEW_STATUS} = 'retained'
              OR {_APPROVAL_STATUS} IN ('rejected', 'excluded')      THEN 'retenido'
            WHEN {_REVIEW_STATUS} IN ('confirmed', 'payment_exception_approved')
              OR {_APPROVAL_STATUS} = 'approved'                     THEN 'procesado'
            WHEN {_REVIEW_STATUS} = 'requested'
              OR {_N_WARNINGS} > 0                                   THEN 'revision'
            ELSE 'procesado'
        END                                       AS estado_circuito,
        {_iban_last4()}                           AS iban_ultimos4,
        b.engine                                  AS engine,
        b.confidence                              AS confianza_agregada,
        b.pages                                   AS paginas,
        b.processing_seconds                      AS segundos_procesamiento,
        b.created_at                              AS procesado_en,
        b.run_created_at                          AS run_creado_en
    FROM b
"""


def _v_field_confidences() -> str:
    # jsonb_each_text explota si el valor no es un objeto: la guarda va DENTRO
    # del LATERAL para que no dependa del orden de evaluacion del planner.
    return f"""
CREATE VIEW {SCHEMA}.v_field_confidences AS
    SELECT
        td.run_id                                 AS run_id,
        td.doc_id                                 AS documento_id,
        fc.campo                                  AS campo,
        CASE WHEN fc.valor ~ '^-?[0-9]+(\\.[0-9]+)?$'
             THEN fc.valor::numeric END           AS confianza
    FROM public.trial_documents td
    CROSS JOIN LATERAL jsonb_each_text(
        CASE WHEN jsonb_typeof(COALESCE(td.field_confidences::jsonb, '{{}}'::jsonb)) = 'object'
             THEN td.field_confidences::jsonb
             ELSE '{{}}'::jsonb END
    ) AS fc(campo, valor)
"""


def _v_review_queue() -> str:
    # 'motivos' son las advertencias del extractor: strings de sistema con
    # NOMBRES de campo (nunca contenido del documento). La nota escrita por el
    # revisor queda fuera: es texto libre humano.
    return f"""
CREATE VIEW {SCHEMA}.v_review_queue AS
{_BASE_CTE}
    SELECT
        b.doc_id                                  AS documento_id,
        b.run_id                                  AS run_id,
        COALESCE({_txt('proveedor_razon_social_legal')},
                 {_txt('proveedor_nombre_comercial')})
                                                  AS proveedor,
        (SELECT string_agg(w.motivo, ' | ' ORDER BY w.orden)
           FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(b.warns) = 'array' THEN b.warns
                     ELSE '[]'::jsonb END)
                WITH ORDINALITY AS w(motivo, orden))
                                                  AS motivos,
        {_N_WARNINGS}                             AS motivos_cantidad,
        COALESCE({_REVIEW_STATUS}, 'pendiente')   AS estado_decision,
        CASE {_REVIEW_STATUS}
            WHEN 'payment_exception_approved' THEN 'autorizador_excepcion'
            WHEN 'confirmed'                  THEN 'revisor_humano'
            WHEN 'retained'                   THEN 'revisor_humano'
            WHEN 'requested'                  THEN 'revisor_humano'
            ELSE NULL
        END                                       AS revisor_rol,
        {_ts(_decision('b.rev', 'timestamp'))}    AS decidido_en,
        CASE WHEN jsonb_typeof(b.rev -> b.doc_id -> 'fields_changed') = 'array'
             THEN jsonb_array_length(b.rev -> b.doc_id -> 'fields_changed')
             ELSE 0 END                           AS campos_corregidos,
        b.created_at                              AS procesado_en
    FROM b
    WHERE {_N_WARNINGS} > 0
       OR {_REVIEW_STATUS} IS NOT NULL
"""


def _v_payment_proposals() -> str:
    return f"""
CREATE VIEW {SCHEMA}.v_payment_proposals AS
{_BASE_CTE}
    SELECT
        b.doc_id                                  AS documento_id,
        b.run_id                                  AS lote_run_id,
        COALESCE({_txt('proveedor_razon_social_legal')},
                 {_txt('proveedor_nombre_comercial')})
                                                  AS proveedor,
        {_tax_id_masked('proveedor_tax_id')}      AS proveedor_tax_id_enmascarado,
        {_num('importe_total')}                   AS importe,
        upper({_txt('moneda')})                   AS moneda,
        {_date('fecha_vencimiento_calculada')}    AS fecha_vencimiento,
        {_ts(_decision('b.apr', 'timestamp'))}    AS aprobado_en,
        'aprobador_pagos'                         AS aprobador_rol,
        {_iban_last4()}                           AS iban_ultimos4,
        b.run_created_at                          AS run_creado_en
    FROM b
    WHERE {_APPROVAL_STATUS} = 'approved'
"""


def _v_run_metrics() -> str:
    # Las columnas tipadas mandan sobre metrics JSON donde existen; el resto
    # sale de metrics (lo escribe trial_repository::_metrics).
    m = "COALESCE(tr.metrics::jsonb, '{}'::jsonb)"
    reconocidos = f"{SCHEMA}.numero_o_null({m} ->> 'invoices')"
    revision = f"{SCHEMA}.numero_o_null({m} ->> 'with_warnings')"
    ok = f"{SCHEMA}.numero_o_null({m} ->> 'successful')"
    conf = f"{SCHEMA}.numero_o_null({m} ->> 'confidence')"
    return f"""
CREATE VIEW {SCHEMA}.v_run_metrics AS
    SELECT
        tr.run_id                                 AS run_id,
        tr.source                                 AS origen,
        tr.created_at                             AS creado_en,
        tr.updated_at                             AS actualizado_en,
        tr.document_count                         AS documentos_procesados,
        {reconocidos}                             AS documentos_reconocidos,
        {ok}                                      AS documentos_sin_error,
        tr.error_count                            AS errores,
        {revision}                                AS derivados_a_revision,
        CASE WHEN {ok} > 0
             THEN round(100.0 * {revision} / {ok}, 2) END
                                                  AS pct_derivado_a_revision,
        {conf}                                    AS confianza_promedio,
        tr.processing_seconds                     AS segundos_procesamiento
    FROM public.trial_runs tr
"""


def _v_exceptions() -> str:
    # Una fila por advertencia. El texto es de sistema (ver extraction/*.py:
    # solo nombres de campo, nunca contenido del documento) y ademas se
    # normaliza a un tipo estable para agrupar en el BI.
    w = "w.advertencia"
    return f"""
CREATE VIEW {SCHEMA}.v_exceptions AS
    SELECT
        td.run_id                                 AS run_id,
        td.doc_id                                 AS documento_id,
        CASE
            WHEN {w} ILIKE 'baja confianza%'            THEN 'baja_confianza'
            WHEN {w} ILIKE '%campos criticos ausentes%' THEN 'campos_criticos_ausentes'
            WHEN {w} ILIKE '%iban%'                     THEN 'iban_invalido'
            WHEN {w} ILIKE '%bic%' OR {w} ILIKE '%swift%' THEN 'bic_invalido'
            WHEN {w} ILIKE '%digitos de control%'       THEN 'cuenta_invalida'
            WHEN {w} ILIKE '%datos bancarios%'          THEN 'datos_bancarios_no_estructurados'
            WHEN {w} ILIKE '%no coincide con el total%' THEN 'descuadre_base_iva_total'
            WHEN {w} ILIKE '%misma entidad%'            THEN 'proveedor_igual_cliente'
            WHEN {w} ILIKE '%document ai%'              THEN 'extractor_degradado'
            WHEN {w} ILIKE '%ocr%'                      THEN 'texto_insuficiente'
            WHEN {w} ILIKE '%other%'                    THEN 'clasificacion_dudosa'
            ELSE 'otra'
        END                                       AS tipo_advertencia,
        CASE
            WHEN {w} ILIKE '%campos criticos ausentes%' THEN 'alta'
            WHEN {w} ILIKE '%no coincide con el total%' THEN 'alta'
            WHEN {w} ILIKE '%iban%'                     THEN 'alta'
            WHEN {w} ILIKE '%misma entidad%'            THEN 'alta'
            WHEN {w} ILIKE 'baja confianza%'            THEN 'media'
            WHEN {w} ILIKE '%ocr%'                      THEN 'media'
            ELSE 'baja'
        END                                       AS severidad,
        {w}                                       AS advertencia,
        td.created_at                             AS procesado_en
    FROM public.trial_documents td
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(COALESCE(td.warnings::jsonb, '[]'::jsonb)) = 'array'
             THEN td.warnings::jsonb
             ELSE '[]'::jsonb END
    ) AS w(advertencia)
"""


COMMENTS: tuple[tuple[str, str], ...] = (
    ("v_documents",
     "Un registro por documento procesado. IBAN y tax_id enmascarados en la "
     "vista; sin texto libre del documento ni identidad del cliente."),
    ("v_field_confidences",
     "Confianza por campo y documento (formato largo) para analisis de calidad."),
    ("v_review_queue",
     "Documentos derivados a revision humana: motivos del extractor, estado de "
     "la decision y ROL del revisor (nunca el nombre propio)."),
    ("v_payment_proposals",
     "Propuestas de pago aprobadas. Aprobar aqui no libera dinero: es la "
     "inclusion del documento en una propuesta controlada."),
    ("v_run_metrics", "Metricas agregadas por corrida/sesion."),
    ("v_exceptions",
     "Excepciones y advertencias por documento, con tipo normalizado y severidad."),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(
        f"COMMENT ON SCHEMA {SCHEMA} IS "
        "'Vistas curadas de solo lectura para BI externo. Sin datos "
        "personales completos ni texto libre de documentos.'")
    # DROP + CREATE (no CREATE OR REPLACE): reemplazar una vista cuya lista de
    # columnas cambio falla; asi la migracion es re-aplicable.
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{view}")
    for funcion in _FUNCIONES:
        op.execute(funcion)
    # Las vistas dependen de las funciones: van despues.
    op.execute(_v_documents())
    op.execute(_v_field_confidences())
    op.execute(_v_review_queue())
    op.execute(_v_payment_proposals())
    op.execute(_v_run_metrics())
    op.execute(_v_exceptions())
    for view, comment in COMMENTS:
        op.execute(f"COMMENT ON VIEW {SCHEMA}.{view} IS '{comment}'")


def downgrade() -> None:
    if not _is_postgres():
        return
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{view}")
    # Las funciones se borran despues de las vistas que las usan.
    for funcion in FUNCIONES_NOMBRES:
        op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{funcion}")
    # RESTRICT y no CASCADE: si el operador dejo objetos propios en el esquema,
    # es preferible que el downgrade falle ruidosamente a destruirlos en
    # silencio. El rol zoho_analytics_ro sobrevive al downgrade (lo administra
    # el runbook, no la migracion).
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} RESTRICT")
