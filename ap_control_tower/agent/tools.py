"""Tools locales, deterministas y de solo lectura para un documento seleccionado."""

from __future__ import annotations

from decimal import Decimal
import json
from typing import Any, Callable

from .privacy import redact_text, safe_document_fields
from ..ui.trial import workflow


def _empty_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "get_document_context",
        "description": (
            "Devuelve el contexto operativo minimizado del documento seleccionado, "
            "sin PDF ni datos bancarios completos."
        ),
        "strict": True,
        "parameters": _empty_parameters(),
    },
    {
        "type": "function",
        "name": "explain_review_reasons",
        "description": (
            "Devuelve los motivos deterministas por los que el documento requiere "
            "o no revisión humana."
        ),
        "strict": True,
        "parameters": _empty_parameters(),
    },
    {
        "type": "function",
        "name": "summarize_document_evidence",
        "description": (
            "Resume la evidencia disponible: extracción, campos críticos, "
            "advertencias, decisiones y controles registrados."
        ),
        "strict": True,
        "parameters": _empty_parameters(),
    },
    {
        "type": "function",
        "name": "suggest_reviewer_actions",
        "description": (
            "Sugiere próximos pasos de revisión basados únicamente en reglas y "
            "evidencia existentes. Nunca ejecuta ni registra decisiones."
        ),
        "strict": True,
        "parameters": _empty_parameters(),
    },
    {
        "type": "function",
        "name": "get_vendor_master_status",
        "description": (
            "Informa si el maestro de proveedores Sage está disponible y el estado "
            "de vinculación del proveedor del documento."
        ),
        "strict": True,
        "parameters": _empty_parameters(),
    },
    {
        "type": "function",
        "name": "get_document_text",
        "description": (
            "Devuelve el texto extraído del PDF del documento (identificadores "
            "sensibles enmascarados), para responder preguntas sobre su contenido. "
            "Es la transcripción del documento, no el PDF: usala para verificar "
            "leyendas, líneas, importes o tipo documental. Puede estar vacío si el "
            "documento no dejó texto legible."
        ),
        "strict": True,
        "parameters": _empty_parameters(),
    },
]


class ReadOnlyDocumentTools:
    """Vista acotada a un único documento ya seleccionado por la UI."""

    def __init__(self, active, result) -> None:
        self.active = active
        self.result = result

    @property
    def doc_id(self) -> str:
        return str(self.result.doc_id)

    def _review_reasons(self) -> list[str]:
        duplicates = workflow.duplicate_doc_ids(self.active.results)
        decision = self.active.review_decisions.get(self.result.doc_id) or {}
        return workflow.review_reasons(
            self.result,
            duplicate=self.result.doc_id in duplicates,
            classification_requested=decision.get("status") == "requested",
        )

    def get_document_context(self) -> dict[str, Any]:
        review = self.active.review_decisions.get(self.result.doc_id) or {}
        approval = self.active.approval_decisions.get(self.result.doc_id) or {}
        return {
            "documento": self.doc_id,
            "campos": safe_document_fields(self.result.document),
            "estado_revision": review.get("status") or "sin_decision",
            "estado_propuesta_pago": approval.get("status") or "sin_decision",
            "alcance": "consulta_de_solo_lectura",
        }

    def explain_review_reasons(self) -> dict[str, Any]:
        reasons = [redact_text(item) for item in self._review_reasons()]
        return {
            "documento": self.doc_id,
            "requiere_revision": bool(reasons),
            "motivos": reasons,
            "regla": (
                "Los motivos provienen de la política determinista del AP Tower; "
                "el modelo no los crea ni los modifica."
            ),
        }

    def summarize_document_evidence(self) -> dict[str, Any]:
        document = self.result.document
        missing = workflow.missing_critical_fields(document)
        warnings = [redact_text(item) for item in (self.result.warnings or [])]
        relevant_confidences = {}
        for field in sorted(workflow.CRITICAL_CONFIDENCE_FIELDS):
            value = (self.result.field_confidences or {}).get(field)
            if value is not None:
                try:
                    relevant_confidences[field] = float(Decimal(str(value)))
                except Exception:
                    continue
        events = [
            {
                "fecha": event.ts,
                "accion": redact_text(event.action, max_length=100),
                "resultado": redact_text(event.result, max_length=100),
            }
            for event in self.active.audit.events
            if event.invoice_id == self.result.doc_id
        ][-12:]
        return {
            "documento": self.doc_id,
            "motor_extraccion": self.result.engine,
            "confianza_general": float(Decimal(str(self.result.confidence))),
            "confianzas_campos_criticos": relevant_confidences,
            "campos_criticos_faltantes": missing,
            "advertencias": warnings,
            "decisiones_registradas": events,
            "cadena_auditoria_integra": bool(self.active.audit.verify_chain()),
            "pdf_enviado_al_modelo": False,
        }

    def suggest_reviewer_actions(self) -> dict[str, Any]:
        document = self.result.document
        reasons = self._review_reasons()
        missing = workflow.missing_critical_fields(document)
        suggestions: list[str] = []
        if missing:
            suggestions.append(
                "Verificar en el documento los campos críticos faltantes: "
                + ", ".join(missing)
                + "."
            )
        lowered = " ".join(reasons).casefold()
        if "duplicad" in lowered:
            suggestions.append(
                "Comparar proveedor, número, importe y fecha con el documento similar."
            )
        if "clasific" in lowered or document.get("document_type") != "invoice":
            suggestions.append(
                "Confirmar el tipo documental antes de evaluar una propuesta de pago."
            )
        if "arca" in lowered or "apócrif" in lowered:
            suggestions.append(
                "Revisar la evidencia ARCA registrada y mantener el documento retenido "
                "si la señal no puede resolverse."
            )
        if "confianza" in lowered:
            suggestions.append(
                "Contrastar visualmente los campos señalados con la factura."
            )
        if not suggestions:
            suggestions.append(
                "Revisar los datos mostrados y continuar con el circuito humano vigente."
            )
        return {
            "documento": self.doc_id,
            "sugerencias": suggestions,
            "ejecuta_acciones": False,
            "puede_aprobar_o_liberar_pagos": False,
        }

    def get_vendor_master_status(self) -> dict[str, Any]:
        summary = getattr(self.active, "supplier_master_summary", {}) or {}
        resolution = (
            getattr(self.active, "supplier_resolutions", {}) or {}
        ).get(self.doc_id)
        if not summary:
            return {
                "estado_maestro": "no_disponible",
                "estado_vinculacion": "no_evaluable",
                "mensaje": (
                    "El maestro de proveedores todavía no está cargado. "
                    "La respuesta no debe inferir una vinculación."
                ),
            }
        if not resolution:
            return {
                "estado_maestro": "disponible",
                "estado_vinculacion": "no_evaluable",
                "mensaje": "No hay una resolución registrada para este documento.",
            }
        return {
            "estado_maestro": "disponible",
            "estado_vinculacion": resolution.get("status") or "no_evaluable",
            "metodo": resolution.get("method"),
            "candidatos": resolution.get("candidate_count"),
            "similitud": resolution.get("score"),
            "tax_id_confirmado": resolution.get("tax_id_confirmed"),
        }

    def get_document_text(self) -> dict[str, Any]:
        raw = getattr(self.result, "source_text", "") or ""
        redacted = redact_text(raw, max_length=8000)
        return {
            "documento": self.doc_id,
            "texto_disponible": bool(redacted),
            "texto_documento": redacted or None,
            "nota": (
                "Transcripción del PDF con identificadores enmascarados. El PDF "
                "binario no se envía al modelo. Si está vacío, el documento no "
                "dejó texto legible y se debe revisar el original."
            ),
        }

    def dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if arguments:
            raise ValueError("Las tools del documento no aceptan argumentos.")
        functions: dict[str, Callable[[], dict[str, Any]]] = {
            "get_document_context": self.get_document_context,
            "explain_review_reasons": self.explain_review_reasons,
            "summarize_document_evidence": self.summarize_document_evidence,
            "suggest_reviewer_actions": self.suggest_reviewer_actions,
            "get_vendor_master_status": self.get_vendor_master_status,
            "get_document_text": self.get_document_text,
        }
        function = functions.get(name)
        if function is None:
            raise ValueError("Tool no permitida.")
        return json.dumps(function(), ensure_ascii=False, default=str)
