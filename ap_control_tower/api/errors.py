"""Manejo de errores de la API: respuestas claras y estructuradas.

Mapea las excepciones del dominio a codigos HTTP:
  - GateViolation / IllegalTransition -> 409 Conflict (transicion invalida)
  - ReviewError                        -> 422 Unprocessable (dato/estado invalido)
  - NotFound                           -> 404
Todas las respuestas de error llevan el correlation_id.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..app import GateViolation, ReviewError
from ..engine.lifecycle import IllegalTransition


class NotFound(RuntimeError):
    pass


def _payload(request: Request, error: str, detail: str) -> dict:
    return {"error": error, "detail": detail,
            "correlation_id": getattr(request.state, "correlation_id", None)}


def register_error_handlers(api) -> None:
    @api.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404,
                            content=_payload(request, "not_found", str(exc)))

    @api.exception_handler(GateViolation)
    async def _gate(request: Request, exc: GateViolation):
        return JSONResponse(status_code=409,
                            content=_payload(request, "gate_violation", str(exc)))

    @api.exception_handler(IllegalTransition)
    async def _illegal(request: Request, exc: IllegalTransition):
        return JSONResponse(status_code=409,
                            content=_payload(request, "illegal_transition", str(exc)))

    @api.exception_handler(ReviewError)
    async def _review(request: Request, exc: ReviewError):
        return JSONResponse(status_code=422,
                            content=_payload(request, "review_error", str(exc)))
