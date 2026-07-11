"""Esquemas Pydantic de la API (validacion estricta de entrada/salida).

Ninguna respuesta expone datos bancarios completos: los IBAN/cuentas/tax_id se
enmascaran en la capa de rutas antes de construir estos modelos.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    error: str
    detail: str
    correlation_id: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    size: int
    total: int


# ------------------------------------------------------------------ requests
class CreateRunRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        description="Opcional. Si se provee y ya existe, se devuelve esa corrida (idempotente).")


class ReviewRequest(BaseModel):
    tipo: str = Field(description="'datos_internos' | 'anticipo'")
    confirmado_por: str = Field(min_length=1, description="Nombre de quien confirma")
    cost_center: str | None = None
    internal_approver: str | None = None
    contract_ref: str | None = None


class ApproveRequest(BaseModel):
    aprobador: str = Field(min_length=1, description="Nombre del aprobador (queda en auditoria)")


class RejectRequest(BaseModel):
    aprobador: str = Field(min_length=1)
    motivo: str = Field(min_length=1)


class ResolveExceptionRequest(BaseModel):
    resuelto_por: str = Field(min_length=1)
    resolucion: str = Field(min_length=1, description="Descripcion de la resolucion")


# ------------------------------------------------------------------ responses
class RunSummary(BaseModel):
    run_id: str
    commit: str
    documentos: int
    en_lote: int
    bloqueadas: int
    retenciones: int
    tareas_conciliacion: int
    anticipos: int
    proximo_ciclo: int
    lotes: list["BatchSummary"]


class BatchSummary(BaseModel):
    fecha_lote: str
    facturas: int
    total: str
    moneda: str
    estado: str


class DocumentView(BaseModel):
    invoice_id: str
    proveedor: str
    numero_factura: str | None
    importe_total: str
    moneda: str
    estado: str
    fase_ciclo_vida: str | None
    control_bloqueante: str | None
    flags: list[str]
    ruta: str
    metodo_pago: str
    lote: str | None
    iban_enmascarado: str | None


class ExceptionView(BaseModel):
    invoice_id: str
    control_id: str
    severidad: str
    owner: str
    detalle: str
    alerta_fraude: bool


class AuditView(BaseModel):
    seq: int
    ts: str
    agent: str
    action: str
    invoice_id: str | None
    control_id: str | None
    result: str | None


class AuditPage(Page[AuditView]):
    cadena_verificada: bool


class MetricsView(BaseModel):
    documentos: int
    en_lote: int
    bloqueadas: int
    retenciones: int
    tareas_conciliacion: int
    anticipos: int
    con_flag: int
    proximo_ciclo: int
    tasa_revision_humana: float
    tasa_bloqueo: float
    total_lotes: str
    lotes_por_estado: dict[str, int]


RunSummary.model_rebuild()
