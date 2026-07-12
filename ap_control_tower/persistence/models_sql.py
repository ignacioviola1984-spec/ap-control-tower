"""Modelo relacional de AP Control Tower (Fase 1).

SQLAlchemy 2.0 declarativo, portable Postgres + SQLite (dev/tests). El dinero
es NUMERIC(18,2); las fechas DATE; la evidencia y los payloads crudos JSON
(JSONB en Postgres). Las cadenas de estado se referencian desde
``ap_control_tower.models`` (fuente unica de verdad; el motor NO importa este
modulo, la dependencia es en un solo sentido).

Restricciones que hace cumplir el esquema:
  - documento con id_interno unico (no se duplica el documento);
  - factura fiscal (proveedor + numero) unica entre facturas ACTIVAS
    (indice parcial: los duplicados bloqueados por C2 no colisionan);
  - integridad referencial (FKs) para evitar relaciones huerfanas;
  - CHECK de estados validos (evita estados invalidos);
  - una factura pertenece a lo sumo a UN lote (unique en la asociacion);
  - la auditoria es append-only (encadenada por hash; el repo no actualiza
    ni borra eventos: modificar una decision auditada rompe la cadena).
La regla "pago sin aprobacion" y "sin liberar lote bloqueado" se hace cumplir
en la capa de repositorios/estados (Fase 2), apoyada por estas FKs.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from .. import models as dom

# ------------------------------------------------------------------ bases
MONEY = Numeric(18, 2)
RATIO = Numeric(6, 4)

# Predicado del indice parcial de unicidad de factura fiscal (Postgres+SQLite).
_FACTURA_ACTIVA_WHERE = text(
    "numero_factura IS NOT NULL AND "
    "(estado_operativo IS NULL OR estado_operativo <> 'bloqueada')"
)


class Base(DeclarativeBase):
    """Base declarativa. Su ``metadata`` la usa Alembic para el autogenerado."""


def _ts_col(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), **kw)


# Estados operativos que el pipeline puede emitir para una factura + los de
# gate/cierre. Fase 2 formaliza la maquina de estados completa; aca se admite
# el conjunto conocido hoy (constantes de dom) para no invalidar la demo.
_DOC_ESTADOS = tuple(sorted({
    dom.STATUS_BLOQUEADA, dom.STATUS_EN_LOTE, dom.STATUS_PROXIMO_CICLO,
    dom.STATUS_PENDIENTE_DATOS_INTERNOS, dom.STATUS_RETENIDO_ALTA_PROVEEDOR,
    dom.STATUS_ANTICIPO_RETENIDO, dom.STATUS_ANTICIPO_PENDIENTE,
    dom.STATUS_ANTICIPO_EXCEPCION, dom.STATUS_DOMICILIACION, dom.STATUS_TARJETA,
    dom.STATUS_OTRO_DOC, dom.STATUS_LOTE_DEVUELTO, dom.STATUS_LIBERADA_AL_BANCO,
    dom.STATUS_CERRADA,
    "recibido", "validando", "en_cola", "procesando", "extraido",
    "controles_en_ejecucion", "requiere_revision", "aprobado",
    "preparado_para_pago", "fallido", "en_cuarentena",
}))

_LOTE_ESTADOS = (
    "propuesto", "revalidado_a", "pendiente_aprobacion_humana", "aprobado",
    "liberado_al_banco", "rechazado", "detenido_por_checker",
)

_EXCEPCION_ESTADOS = ("abierta", "en_revision", "resuelta", "descartada")


def _in(col: str, values) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


# ------------------------------------------------------------------ proveedores
class Proveedor(Base):
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo_interno: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    nombre_legal: Mapped[str | None] = mapped_column(String(255))
    nombre_comercial: Mapped[str | None] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(32))
    codigo_pais: Mapped[str] = mapped_column(String(2), default="ES")
    estado: Mapped[str] = mapped_column(String(24), default="activo")
    intercompany: Mapped[bool] = mapped_column(Boolean, default=False)
    categoria: Mapped[str | None] = mapped_column(String(64))
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    razon_social_confirmada: Mapped[bool] = mapped_column(Boolean, default=True)
    # Datos bancarios AUTORIZADOS (completos; se muestran enmascarados).
    iban_autorizado: Mapped[str | None] = mapped_column(String(40))
    banco_autorizado: Mapped[str | None] = mapped_column(String(128))
    sepa_mandate_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col(onupdate=func.now())

    ordenes: Mapped[list["OrdenCompra"]] = relationship(back_populates="proveedor")
    facturas: Mapped[list["Factura"]] = relationship(back_populates="proveedor")
    cambios_bancarios: Mapped[list["ProveedorCambioBancario"]] = relationship(
        back_populates="proveedor", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(_in("estado", ("activo", "retenido", "baja")),
                        name="ck_proveedor_estado"),
    )


class ProveedorCambioBancario(Base):
    """Historial de cambios de informacion sensible del proveedor (bancaria).

    El alta/cambio de datos bancarios exige doble aprobacion humana (dominio
    del negocio); aca se conserva la traza completa e inmutable del cambio.
    """
    __tablename__ = "proveedor_cambios_bancarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(
        ForeignKey("proveedores.id", ondelete="CASCADE"), index=True)
    iban_anterior: Mapped[str | None] = mapped_column(String(40))
    iban_nuevo: Mapped[str | None] = mapped_column(String(40))
    banco_anterior: Mapped[str | None] = mapped_column(String(128))
    banco_nuevo: Mapped[str | None] = mapped_column(String(128))
    motivo: Mapped[str | None] = mapped_column(Text)
    cambiado_por: Mapped[str | None] = mapped_column(String(128))
    aprobado_por_1: Mapped[str | None] = mapped_column(String(128))
    aprobado_por_2: Mapped[str | None] = mapped_column(String(128))
    ts: Mapped[datetime] = _ts_col()

    proveedor: Mapped[Proveedor] = relationship(back_populates="cambios_bancarios")


# ------------------------------------------------------------------ ordenes de compra
class OrdenCompra(Base):
    __tablename__ = "ordenes_compra"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero_oc: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    entidad: Mapped[str | None] = mapped_column(String(128))
    moneda: Mapped[str] = mapped_column(String(3), default="EUR")
    importe_autorizado: Mapped[Decimal] = mapped_column(MONEY)
    saldo: Mapped[Decimal | None] = mapped_column(MONEY)
    estado: Mapped[str] = mapped_column(String(24), default="aprobada")
    vigencia_desde: Mapped[date | None] = mapped_column(Date)
    vigencia_hasta: Mapped[date | None] = mapped_column(Date)
    gl_account: Mapped[str | None] = mapped_column(String(16))
    mgmt_category: Mapped[str | None] = mapped_column(String(64))
    project_code: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _ts_col()

    proveedor: Mapped[Proveedor] = relationship(back_populates="ordenes")
    lineas: Mapped[list["OrdenCompraLinea"]] = relationship(
        back_populates="orden", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(_in("estado", ("aprobada", "borrador", "cerrada")),
                        name="ck_oc_estado"),
    )


class OrdenCompraLinea(Base):
    __tablename__ = "oc_lineas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    orden_id: Mapped[int] = mapped_column(
        ForeignKey("ordenes_compra.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[str] = mapped_column(String(32))
    descripcion: Mapped[str | None] = mapped_column(Text)
    importe: Mapped[Decimal] = mapped_column(MONEY)

    orden: Mapped[OrdenCompra] = relationship(back_populates="lineas")

    __table_args__ = (
        UniqueConstraint("orden_id", "line_id", name="uq_oc_linea"),
    )


# ------------------------------------------------------------------ documentos
class Documento(Base):
    """Unidad de ingesta: cualquier documento recibido (factura, proforma, OC,
    otro). El resultado bruto de Document AI se referencia, no se inserta el PDF.
    """
    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_interno: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    nombre_archivo: Mapped[str | None] = mapped_column(String(255))
    hash_archivo: Mapped[str | None] = mapped_column(String(64), index=True)
    tipo_documental: Mapped[str] = mapped_column(String(40), default=dom.DOC_INVOICE)
    origen: Mapped[str] = mapped_column(String(32), default="synthetic")
    fecha_recepcion: Mapped[date | None] = mapped_column(Date)
    estado_procesamiento: Mapped[str] = mapped_column(String(40), default="recibido")
    # Fase canonica del ciclo de vida (Fase 2). Derivada de estado_procesamiento
    # via engine.lifecycle.phase_for_status; las transiciones las valida
    # persistence.state_service contra la matriz del ciclo de vida.
    fase_ciclo_vida: Mapped[str | None] = mapped_column(String(32), index=True)
    ubicacion_segura: Mapped[str | None] = mapped_column(String(512))
    cantidad_paginas: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(64))
    documentai_raw_ref: Mapped[str | None] = mapped_column(String(512))
    documentai_processor_version: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col(onupdate=func.now())

    factura: Mapped["Factura | None"] = relationship(
        back_populates="documento", uselist=False)
    controles: Mapped[list["ControlEjecutado"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan")
    excepciones: Mapped[list["Excepcion"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan")
    revisiones: Mapped[list["RevisionHumana"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(_in("tipo_documental",
                            (dom.DOC_INVOICE, dom.DOC_PROFORMA, dom.DOC_OTHER, "oc")),
                        name="ck_documento_tipo"),
        CheckConstraint(_in("estado_procesamiento", _DOC_ESTADOS),
                        name="ck_documento_estado"),
    )


# ------------------------------------------------------------------ facturas
class Factura(Base):
    __tablename__ = "facturas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos.id", ondelete="CASCADE"), unique=True, index=True)
    proveedor_id: Mapped[int] = mapped_column(ForeignKey("proveedores.id"), index=True)
    comprador: Mapped[str | None] = mapped_column(String(128))
    numero_factura: Mapped[str | None] = mapped_column(String(64))
    fecha_emision: Mapped[date | None] = mapped_column(Date)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date)
    moneda: Mapped[str] = mapped_column(String(3), default="EUR")
    importe_neto: Mapped[Decimal | None] = mapped_column(MONEY)
    importe_impuestos: Mapped[Decimal | None] = mapped_column(MONEY)
    importe_descuentos: Mapped[Decimal | None] = mapped_column(MONEY)
    importe_gastos: Mapped[Decimal | None] = mapped_column(MONEY)
    importe_total: Mapped[Decimal] = mapped_column(MONEY)
    importe_pendiente: Mapped[Decimal | None] = mapped_column(MONEY)
    condicion_pago: Mapped[str | None] = mapped_column(String(128))
    referencia_proyecto: Mapped[str | None] = mapped_column(String(48))
    referencia_contrato: Mapped[str | None] = mapped_column(String(48))
    referencia_orden: Mapped[str | None] = mapped_column(String(48))  # po_ref
    ruta_ap: Mapped[str] = mapped_column(String(24), default="po")
    metodo_pago: Mapped[str] = mapped_column(String(32), default="transferencia")
    tratamiento_iva: Mapped[str] = mapped_column(String(48), default="nacional")
    nivel_confianza: Mapped[Decimal | None] = mapped_column(RATIO)
    estado_operativo: Mapped[str | None] = mapped_column(String(40), index=True)
    # Cuenta destino que pide el documento (completa; se muestra enmascarada).
    iban_en_factura: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col(onupdate=func.now())

    documento: Mapped[Documento] = relationship(back_populates="factura")
    proveedor: Mapped[Proveedor] = relationship(back_populates="facturas")

    __table_args__ = (
        CheckConstraint(_in("ruta_ap", ("po", "non_po", "anticipo", "otro")),
                        name="ck_factura_ruta"),
        # Factura fiscal unica por proveedor SOLO entre facturas activas: los
        # duplicados que C2 bloquea (estado 'bloqueada') no colisionan.
        Index(
            "uq_factura_activa",
            "proveedor_id", "numero_factura",
            unique=True,
            sqlite_where=_FACTURA_ACTIVA_WHERE,
            postgresql_where=_FACTURA_ACTIVA_WHERE,
        ),
    )


# ------------------------------------------------------------------ controles y excepciones
class ControlEjecutado(Base):
    __tablename__ = "controles_ejecutados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos.id", ondelete="CASCADE"), index=True)
    control_id: Mapped[str] = mapped_column(String(48))
    version_control: Mapped[str] = mapped_column(String(16), default="v1")
    passed: Mapped[bool] = mapped_column(Boolean)
    resultado: Mapped[str] = mapped_column(String(24))  # pasa | falla-hard | flag-soft
    severidad: Mapped[str] = mapped_column(String(8))   # hard | soft
    detalle: Mapped[str | None] = mapped_column(Text)   # motivo
    evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    checker: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    fecha_ejecucion: Mapped[datetime] = _ts_col()

    documento: Mapped[Documento] = relationship(back_populates="controles")


class Excepcion(Base):
    __tablename__ = "excepciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos.id", ondelete="CASCADE"), index=True)
    control_id: Mapped[str] = mapped_column(String(48))
    severidad: Mapped[str] = mapped_column(String(8))
    owner: Mapped[str | None] = mapped_column(String(128))
    detalle: Mapped[str | None] = mapped_column(Text)  # motivo
    evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    fraud_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    estado_resolucion: Mapped[str] = mapped_column(String(16), default="abierta")
    usuario_responsable: Mapped[str | None] = mapped_column(String(128))
    creada_en: Mapped[datetime] = _ts_col()
    resuelta_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documento: Mapped[Documento] = relationship(back_populates="excepciones")

    __table_args__ = (
        CheckConstraint(_in("estado_resolucion", _EXCEPCION_ESTADOS),
                        name="ck_excepcion_estado"),
    )


# ------------------------------------------------------------------ revision humana
class RevisionHumana(Base):
    __tablename__ = "revision_humana"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos.id", ondelete="CASCADE"), index=True)
    campo_original: Mapped[str | None] = mapped_column(String(64))
    valor_extraido: Mapped[str | None] = mapped_column(Text)
    valor_corregido: Mapped[str | None] = mapped_column(Text)
    motivo_correccion: Mapped[str | None] = mapped_column(Text)
    revisor: Mapped[str | None] = mapped_column(String(128))
    decision: Mapped[str | None] = mapped_column(String(32))
    evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    fecha: Mapped[datetime] = _ts_col()

    documento: Mapped[Documento] = relationship(back_populates="revisiones")


# ------------------------------------------------------------------ lotes, aprobaciones, pagos
class LotePago(Base):
    __tablename__ = "lotes_pago"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha_lote: Mapped[date] = mapped_column(Date, index=True)
    estado: Mapped[str] = mapped_column(String(32), default="propuesto")
    moneda: Mapped[str] = mapped_column(String(3), default="EUR")
    total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    sign_off_a: Mapped[dict | None] = mapped_column(JSON)
    sign_off_b: Mapped[dict | None] = mapped_column(JSON)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = _ts_col()

    facturas: Mapped[list["LoteFactura"]] = relationship(
        back_populates="lote", cascade="all, delete-orphan")
    aprobaciones: Mapped[list["Aprobacion"]] = relationship(
        back_populates="lote", cascade="all, delete-orphan")
    pagos: Mapped[list["Pago"]] = relationship(
        back_populates="lote", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(_in("estado", _LOTE_ESTADOS), name="ck_lote_estado"),
    )


class LoteFactura(Base):
    """Asociacion lote<->factura. Una factura pertenece a lo sumo a UN lote."""
    __tablename__ = "lote_facturas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lote_id: Mapped[int] = mapped_column(
        ForeignKey("lotes_pago.id", ondelete="CASCADE"), index=True)
    factura_id: Mapped[int] = mapped_column(
        ForeignKey("facturas.id", ondelete="CASCADE"))

    lote: Mapped[LotePago] = relationship(back_populates="facturas")

    __table_args__ = (
        UniqueConstraint("factura_id", name="uq_factura_un_solo_lote"),
    )


class Aprobacion(Base):
    """Decision del gate humano sobre un lote (aprobar / rechazar)."""
    __tablename__ = "aprobaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lote_id: Mapped[int] = mapped_column(
        ForeignKey("lotes_pago.id", ondelete="CASCADE"), index=True)
    aprobador: Mapped[str] = mapped_column(String(128))
    nivel_requerido: Mapped[str | None] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))  # aprobar | rechazar
    motivo: Mapped[str | None] = mapped_column(Text)
    evidencia_gate: Mapped[dict] = mapped_column(JSON, default=dict)
    fecha: Mapped[datetime] = _ts_col()

    lote: Mapped[LotePago] = relationship(back_populates="aprobaciones")

    __table_args__ = (
        CheckConstraint(_in("decision", ("aprobar", "rechazar")),
                        name="ck_aprobacion_decision"),
    )


class Pago(Base):
    """Pago preparado/liberado. Solo debe existir si su lote fue aprobado
    (se hace cumplir en la capa de estados de Fase 2, apoyada por la FK a lote).
    """
    __tablename__ = "pagos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id"), index=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes_pago.id"), index=True)
    estado_pago: Mapped[str] = mapped_column(String(24), default="preparado")
    importe: Mapped[Decimal] = mapped_column(MONEY)
    value_date: Mapped[date | None] = mapped_column(Date)
    referencia_externa: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts_col()

    lote: Mapped[LotePago] = relationship(back_populates="pagos")

    __table_args__ = (
        CheckConstraint(
            _in("estado_pago", ("preparado", "liberado", "conciliado", "rechazado")),
            name="ck_pago_estado"),
    )


# ------------------------------------------------------------------ auditoria
class AuditoriaEvento(Base):
    """Auditoria encadenada por hash. Append-only: el repositorio nunca
    actualiza ni borra filas; alterar una implica recomputar toda la cadena.
    Conserva la semantica de ``ap_control_tower.audit.AuditTrail``.
    """
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    commit: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[str] = mapped_column(String(40))  # ISO del evento original
    actor: Mapped[str] = mapped_column(String(64))         # agente / usuario
    accion: Mapped[str] = mapped_column(String(64))
    entidad_tipo: Mapped[str | None] = mapped_column(String(32))
    entidad_id: Mapped[str | None] = mapped_column(String(255))
    invoice_id: Mapped[str | None] = mapped_column(String(255), index=True)
    control_id: Mapped[str | None] = mapped_column(String(48))
    resultado: Mapped[str | None] = mapped_column(String(48))
    estado_anterior: Mapped[str | None] = mapped_column(String(48))
    estado_posterior: Mapped[str | None] = mapped_column(String(48))
    correlation_id: Mapped[str | None] = mapped_column(String(255), index=True)
    evidencia: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_auditoria_run_seq"),
    )


# ------------------------------------------------------------------ trial real
class TrialRun(Base):
    """Corrida de documentos reales del Trial, sin almacenar el PDF original."""

    __tablename__ = "trial_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col(onupdate=func.now())
    source: Mapped[str] = mapped_column(String(32), default="trial")
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    review_decisions: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_decisions: Mapped[dict] = mapped_column(JSON, default=dict)

    documents: Mapped[list["TrialDocument"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")


class TrialDocument(Base):
    """Resultado estructurado y enmascarado; nunca contiene bytes del PDF."""

    __tablename__ = "trial_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("trial_runs.run_id", ondelete="CASCADE"), index=True)
    doc_id: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="carga-manual")
    engine: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int] = mapped_column(Integer, default=0)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[Decimal] = mapped_column(RATIO, default=Decimal("0"))
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    document: Mapped[dict] = mapped_column(JSON, default=dict)
    field_confidences: Mapped[dict] = mapped_column(JSON, default=dict)
    processing_seconds: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), default=Decimal("0"))
    created_at: Mapped[datetime] = _ts_col()

    run: Mapped[TrialRun] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("run_id", "doc_id", name="uq_trial_run_document"),
    )
