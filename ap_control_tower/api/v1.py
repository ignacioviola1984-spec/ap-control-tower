"""Rutas de la API v1 sobre la capa de aplicacion (Fase 4).

Operaciones controladas para ERP/portales: crear corrida, consultar
estado/factura/excepciones/auditoria/metricas, registrar correccion humana,
resolver excepcion, aprobar/rechazar/liberar/cerrar lote y cargar+procesar un
documento real. Validacion via Pydantic; idempotencia en operaciones sensibles;
paginacion; correlacion; datos bancarios SIEMPRE enmascarados.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, UploadFile

from .. import app as appsvc
from ..models import Dataset
from ..persistence.masking import mask_account, mask_iban
from . import views
from .deps import Pagination, get_dataset, get_registry, pagination
from .errors import NotFound
from .registry import RunRegistry
from .schemas import (
    ApproveRequest,
    AuditPage,
    AuditView,
    BatchSummary,
    CreateRunRequest,
    DocumentView,
    ExceptionView,
    MetricsView,
    Page,
    RejectRequest,
    ResolveExceptionRequest,
    ReviewRequest,
    RunSummary,
)

router = APIRouter(prefix="/v1")

Reg = Annotated[RunRegistry, Depends(get_registry)]
Ds = Annotated[Dataset, Depends(get_dataset)]
Pg = Annotated[Pagination, Depends(pagination)]


def _run_or_404(reg: RunRegistry, run_id: str) -> dict:
    run = reg.get(run_id)
    if run is None:
        raise NotFound(f"corrida inexistente: {run_id}")
    return run


def _paginate(items: list, pg: Pagination) -> Page:
    return Page(items=items[pg.start:pg.end], page=pg.page, size=pg.size,
               total=len(items))


def _corr(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


# ------------------------------------------------------------------ corridas
@router.post("/runs", response_model=RunSummary, status_code=201,
             summary="Crear/iniciar una corrida del mes (procesamiento)")
def create_run(body: CreateRunRequest, reg: Reg, dataset: Ds) -> RunSummary:
    run = reg.create_run(dataset, run_id=body.run_id)
    return views.run_summary(run)


@router.get("/runs", summary="Listar run_ids en memoria")
def list_runs(reg: Reg) -> dict:
    return {"run_ids": reg.list_run_ids()}


@router.get("/runs/{run_id}", response_model=RunSummary,
            summary="Estado/resumen de una corrida")
def get_run(run_id: str, reg: Reg) -> RunSummary:
    return views.run_summary(_run_or_404(reg, run_id))


@router.get("/runs/{run_id}/metrics", response_model=MetricsView,
            summary="Metricas operativas de la corrida")
def get_metrics(run_id: str, reg: Reg, dataset: Ds) -> MetricsView:
    return views.metrics_view(dataset, _run_or_404(reg, run_id))


# ------------------------------------------------------------------ documentos / facturas
@router.get("/runs/{run_id}/documents", response_model=Page[DocumentView],
            summary="Listar documentos/facturas (paginado, banca enmascarada)")
def list_documents(run_id: str, reg: Reg, dataset: Ds, pg: Pg) -> Page[DocumentView]:
    run = _run_or_404(reg, run_id)
    return _paginate(views.all_document_views(dataset, run), pg)


@router.get("/runs/{run_id}/documents/{invoice_id}", response_model=DocumentView,
            summary="Detalle de factura y campos extraidos")
def get_document(run_id: str, invoice_id: str, reg: Reg, dataset: Ds) -> DocumentView:
    run = _run_or_404(reg, run_id)
    view = views.document_view(dataset, run, invoice_id)
    if view is None:
        raise NotFound(f"documento inexistente en la corrida: {invoice_id}")
    return view


# ------------------------------------------------------------------ excepciones
@router.get("/runs/{run_id}/exceptions", response_model=Page[ExceptionView],
            summary="Listar excepciones (bloqueos por control)")
def list_exceptions(run_id: str, reg: Reg, pg: Pg) -> Page[ExceptionView]:
    run = _run_or_404(reg, run_id)
    return _paginate(views.exception_views(run), pg)


@router.post("/runs/{run_id}/exceptions/{invoice_id}/resolve",
             summary="Registrar la resolucion de una excepcion (auditada)")
def resolve_exception(run_id: str, invoice_id: str, body: ResolveExceptionRequest,
                      reg: Reg, request: Request) -> dict:
    run = _run_or_404(reg, run_id)
    exc = next((e for e in run["result"].exceptions if e.invoice_id == invoice_id), None)
    if exc is None:
        raise NotFound(f"sin excepcion para {invoice_id} en esta corrida")
    run["audit"].add(agent="api", action="resolucion-excepcion",
                     invoice_id=invoice_id, control_id=exc.control_id,
                     result="resuelta",
                     evidence={"resuelto_por": body.resuelto_por,
                               "resolucion": body.resolucion,
                               "correlation_id": _corr(request)})
    return {"invoice_id": invoice_id, "control_id": exc.control_id,
            "estado": "resuelta", "resuelto_por": body.resuelto_por}


# ------------------------------------------------------------------ revision humana
@router.post("/runs/{run_id}/documents/{invoice_id}/review",
             summary="Registrar correccion humana (datos internos o anticipo)")
def register_review(run_id: str, invoice_id: str, body: ReviewRequest,
                    reg: Reg, dataset: Ds) -> dict:
    run = _run_or_404(reg, run_id)
    if body.tipo == "anticipo":
        status = appsvc.approve_anticipo(dataset, run, confirmed_by=body.confirmado_por,
                                         invoice_id=invoice_id)
    elif body.tipo == "datos_internos":
        status = appsvc.confirm_internal_data(
            dataset, run, confirmed_by=body.confirmado_por, invoice_id=invoice_id,
            cost_center=body.cost_center or "", internal_approver=body.internal_approver or "",
            contract_ref=body.contract_ref or "")
    else:
        raise NotFound(f"tipo de revision desconocido: {body.tipo}")
    return {"invoice_id": invoice_id, "estado": status}


# ------------------------------------------------------------------ lotes / gate
@router.get("/runs/{run_id}/batches", response_model=list[BatchSummary],
            summary="Listar lotes de pago y su estado")
def list_batches(run_id: str, reg: Reg) -> list[BatchSummary]:
    return views.batch_summaries(_run_or_404(reg, run_id))


def _batch_or_404(run: dict, iso: str):
    wf = run["workflows"].get(iso)
    if wf is None:
        raise NotFound(f"lote inexistente: {iso}")
    return wf


@router.post("/runs/{run_id}/batches/{iso}/approve", response_model=BatchSummary,
             summary="Aprobar y liberar un lote (gate humano; idempotente)")
def approve_batch(run_id: str, iso: str, body: ApproveRequest, reg: Reg, request: Request,
                  idempotency_key: Annotated[str | None, Header()] = None) -> BatchSummary:
    run = _run_or_404(reg, run_id)
    cached = reg.idempotent(run_id, idempotency_key)
    if cached is not None:
        return cached
    wf = _batch_or_404(run, iso)
    # idempotencia natural: si ya esta liberado, no re-aprobar
    if wf.state != appsvc.ESTADO_LIBERADO:
        appsvc.approve_and_release(run, iso, body.aprobador)
    result = views.batch_summaries(run)
    summary = next(b for b in result if b.fecha_lote == iso)
    reg.remember(run_id, idempotency_key, summary)
    return summary


@router.post("/runs/{run_id}/batches/{iso}/reject", response_model=BatchSummary,
             summary="Rechazar y devolver un lote")
def reject_batch(run_id: str, iso: str, body: RejectRequest, reg: Reg,
                 idempotency_key: Annotated[str | None, Header()] = None) -> BatchSummary:
    run = _run_or_404(reg, run_id)
    cached = reg.idempotent(run_id, idempotency_key)
    if cached is not None:
        return cached
    wf = _batch_or_404(run, iso)
    if wf.state != appsvc.ESTADO_RECHAZADO:
        appsvc.reject_batch(run, iso, body.aprobador, body.motivo)
    summary = next(b for b in views.batch_summaries(run) if b.fecha_lote == iso)
    reg.remember(run_id, idempotency_key, summary)
    return summary


@router.post("/runs/{run_id}/batches/{iso}/close", response_model=BatchSummary,
             summary="Cerrar un lote liberado (conciliacion pago vs pasivo)")
def close_batch(run_id: str, iso: str, reg: Reg) -> BatchSummary:
    run = _run_or_404(reg, run_id)
    _batch_or_404(run, iso)
    if iso not in run["closing_reports"]:
        appsvc.close_batch(run, iso)
    return next(b for b in views.batch_summaries(run) if b.fecha_lote == iso)


# ------------------------------------------------------------------ auditoria
@router.get("/runs/{run_id}/audit", response_model=AuditPage,
            summary="Consultar el audit trail (paginado, cadena verificada)")
def get_audit(run_id: str, reg: Reg, pg: Pg) -> AuditPage:
    run = _run_or_404(reg, run_id)
    audit = run["audit"]
    items = [AuditView(seq=e.seq, ts=e.ts, agent=e.agent, action=e.action,
                       invoice_id=e.invoice_id, control_id=e.control_id, result=e.result)
             for e in audit.events]
    return AuditPage(items=items[pg.start:pg.end], page=pg.page, size=pg.size,
                     total=len(items), cadena_verificada=audit.verify_chain())


# ------------------------------------------------------------------ carga documental real
@router.post("/documents", summary="Cargar y procesar un documento real (extraccion)")
async def upload_document(file: UploadFile) -> dict:
    data = await file.read()
    result = appsvc.process_uploaded_document(file.filename or "documento.pdf", data)
    doc = dict(result.document)
    # datos bancarios SIEMPRE enmascarados en la respuesta
    if doc.get("iban"):
        doc["iban"] = mask_iban(doc["iban"])
    if doc.get("proveedor_cuenta_bancaria"):
        doc["proveedor_cuenta_bancaria"] = mask_account(doc["proveedor_cuenta_bancaria"])
    return {"archivo": result.doc_id, "motor": result.engine,
            "document_type": doc.get("document_type"),
            "confidence": str(result.confidence), "pages": result.pages,
            "warnings": result.warnings, "document": doc}
