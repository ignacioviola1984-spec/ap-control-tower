"""Rutas de la API v1 sobre la capa de aplicacion (Fase 4).

Operaciones controladas para ERP/portales: crear corrida, consultar
estado/factura/excepciones/auditoria/metricas, registrar correccion humana,
resolver excepcion, aprobar/rechazar/liberar/cerrar lote y cargar+procesar un
documento real. Validacion via Pydantic; idempotencia en operaciones sensibles;
paginacion; correlacion; datos bancarios SIEMPRE enmascarados.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Request, Response, UploadFile

from .. import app as appsvc
from ..models import Dataset
from ..worker import JobRecord, JobService
from . import views
from .deps import Pagination, get_dataset, get_job_service, get_registry, pagination
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
    TaskView,
)

router = APIRouter(prefix="/v1")

Reg = Annotated[RunRegistry, Depends(get_registry)]
Ds = Annotated[Dataset, Depends(get_dataset)]
Pg = Annotated[Pagination, Depends(pagination)]
Jobs = Annotated[JobService, Depends(get_job_service)]


def _task_view(rec: JobRecord) -> TaskView:
    return TaskView(id=rec.id, name=rec.name, status=rec.status,
                    attempts=rec.attempts, max_attempts=rec.max_attempts,
                    error=rec.error,
                    result=rec.result if isinstance(rec.result, dict) else None)


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


# ------------------------------------------------------------------ carga documental (async)
@router.post("/documents", response_model=TaskView, status_code=202,
             summary="Cargar un documento real: encola su procesamiento (async)")
async def upload_document(file: UploadFile, jobs: Jobs, response: Response) -> TaskView:
    """Encola la extraccion en la cola de tareas y devuelve 202 + task_id. El
    resultado se consulta en GET /v1/tasks/{id}. Sin broker corre inline; con
    Celery/Redis corre en un worker (no bloquea). Idempotente por contenido."""
    data = await file.read()
    rec = jobs.submit_document(file.filename or "documento.pdf", data)
    response.headers["Location"] = f"/v1/tasks/{rec.id}"
    return _task_view(rec)


# ------------------------------------------------------------------ tareas de la cola
@router.get("/tasks/{task_id}", response_model=TaskView,
            summary="Estado y resultado de una tarea de la cola")
def get_task(task_id: str, jobs: Jobs) -> TaskView:
    rec = jobs.get(task_id)
    if rec is None:
        raise NotFound(f"tarea inexistente: {task_id}")
    return _task_view(rec)


@router.get("/tasks", summary="Listar tareas en dead-letter (fallos)")
def list_dead_letters(jobs: Jobs) -> dict:
    return {"dead_letter": [_task_view(r) for r in jobs.dead_letters()]}


@router.post("/tasks/{task_id}/reprocess", response_model=TaskView,
             summary="Reprocesar manualmente una tarea en dead-letter (autorizado)")
def reprocess_task(task_id: str, jobs: Jobs,
                   solicitado_por: str = Body(embed=True, min_length=1)) -> TaskView:
    rec = jobs.reprocess(task_id, requested_by=solicitado_por)
    if rec is None:
        raise NotFound(f"tarea inexistente: {task_id}")
    return _task_view(rec)
