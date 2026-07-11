"""Factory de la API interna de AP Control Tower (FastAPI, Fase 4).

Separada de la UI: expone la capa de aplicacion por HTTP para ERP/portales.
Documentacion automatica en /docs y /openapi.json. Versionado bajo /v1. Cada
request lleva un correlation_id (header X-Correlation-ID, generado si falta).

Arranque local:
    uvicorn ap_control_tower.api.main:app --port 8000
    # docs interactivos en http://localhost:8000/docs
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request

from .errors import register_error_handlers
from .v1 import router as v1_router

API_VERSION = "1.0.0"


def create_app() -> FastAPI:
    api = FastAPI(
        title="AP Control Tower API",
        version=API_VERSION,
        description=("API interna del sistema maker-checker de Cuentas a Pagar. "
                     "Datos bancarios enmascarados; el gate de pago exige aprobacion "
                     "humana registrada."),
    )

    @api.middleware("http")
    async def _correlation(request: Request, call_next):
        cid = request.headers.get("X-Correlation-ID") or f"cid-{uuid.uuid4().hex[:16]}"
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response

    register_error_handlers(api)
    api.include_router(v1_router)

    @api.get("/healthz", summary="Health check", tags=["infra"])
    def healthz() -> dict:
        return {"status": "ok", "version": API_VERSION}

    return api


app = create_app()
