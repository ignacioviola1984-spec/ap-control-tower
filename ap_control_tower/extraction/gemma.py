"""Adaptador Gemma 4 local (via Ollama) para la extraccion de facturas.

Motor primario de extraccion a costo marginal cero: las paginas del PDF se
renderizan a imagenes y se procesan con Gemma 4 (vision) corriendo en la
propia infraestructura. Google Document AI queda como fallback opcional,
solo para las facturas que no pasan la validacion.

El contrato es el mismo esquema v2 (``schema.FIELD_ORDER``): el JSON Schema
que se envia a Ollama como ``format`` se genera desde el esquema, igual que
el prompt, asi que no puede driftear.

Configuracion (variables de entorno):
    OLLAMA_URL        default http://localhost:11434
    GEMMA_MODEL       default gemma3:4b    (ver nota sobre gemma4 abajo)
    GEMMA_RENDER_DPI  default 180          (subir a 220 con escaneos flojos)
    GEMMA_MAX_PAGES   default 5
    GEMMA_TIMEOUT     default 600 (segundos)
    GEMMA_NUM_CTX     default 16384 (el default de Ollama, 4096, trunca el
                      prompt + imagen y el modelo devuelve todo null)
    GEMMA_DISABLED    definirla (cualquier valor) desactiva el motor

NOTA (2026-07-21): el default es gemma3:4b, no gemma4, porque la familia
gemma4 esta rota en Ollama 0.32.1. Medido en esta maquina, misma imagen
(factura sintetica A4 renderizada a 180 DPI) para todos los modelos:

    gemma3:4b          transcribe perfecto, 21 s, ~256 tokens de imagen
    qwen2.5vl:3b       transcribe perfecto, 39 s, ~3900 tokens de imagen
    gemma4:e2b         carga pero NO lee la imagen: devuelve alucinaciones
                       ("un formulario o tabla con muchas filas y columnas"
                       para una imagen de 3 lineas), pese a que /api/show
                       declara la capability "vision". Con el prompt de
                       extraccion eso da todos los campos en null, que es la
                       respuesta correcta de la regla anti-alucinacion.
    gemma4:e4b-it-qat  ni siquiera carga; el llama-server muere con
                       GGML_ASSERT(n_inputs < GGML_SCHED_MAX_SPLIT_INPUTS)
                       failed, tambien en peticiones de texto solo.
    gemma4:12b         sin medir (>10 min por factura sin GPU).

Descartado como causa del all-null de gemma4:e2b: num_ctx (falla igual con
8192 y con 16384), el JSON schema en "format" (falla igual sin el y con
prompt minimo) y la resolucion (falla igual con recorte y a 300 DPI; subir
el DPI no cambia nada porque la imagen se reescala al mismo tile). El
adaptador no tenia ningun bug: el modelo simplemente no ve la imagen.

Reevaluar gemma4 cuando Ollama actualice el soporte: alcanza con volver a
correr el smoke con GEMMA_MODEL=gemma4:e4b-it-qat. ENGINE_NAME se mantiene
para no romper el mapeo de la UI ni las salidas ya generadas.
"""

from __future__ import annotations

import base64
import io
import json
import os
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .document_ai import (
    _result_confidence,
    _validate_invoice,
    extract_uploaded_document as _document_ai_flow,
    is_document_ai_configured,
)
from .pdf_poc import PocResult, extract_document, read_pdf_bytes
from .prompt import build_extraction_prompt
from .schema import (
    ENUM_VALUES,
    FIELD_KINDS,
    FIELD_ORDER,
    compute_due_date,
    empty_document,
)

ENGINE_NAME = "gemma4_local_ollama"

_AMOUNT_FIELDS = tuple(f for f, k in FIELD_KINDS.items() if k == "amount")


@dataclass(frozen=True)
class GemmaConfig:
    url: str
    model: str
    dpi: int
    max_pages: int
    timeout: int
    num_ctx: int

    @classmethod
    def from_env(cls) -> "GemmaConfig | None":
        if os.getenv("GEMMA_DISABLED"):
            return None
        return cls(
            url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
            model=os.getenv("GEMMA_MODEL", "gemma3:4b"),
            dpi=int(os.getenv("GEMMA_RENDER_DPI", "180")),
            max_pages=int(os.getenv("GEMMA_MAX_PAGES", "5")),
            timeout=int(os.getenv("GEMMA_TIMEOUT", "600")),
            num_ctx=int(os.getenv("GEMMA_NUM_CTX", "16384")),
        )


def is_gemma_configured() -> bool:
    return GemmaConfig.from_env() is not None


# ------------------------------------------------------------- json schema

def _json_type(kind: str, field: str) -> dict:
    """Traduce el tipo logico del esquema v2 a JSON Schema para Ollama."""
    if kind == "enum":
        return {"enum": list(ENUM_VALUES[field]) + [None]}
    if kind == "amount":
        return {"type": ["number", "string", "null"]}
    if kind == "bool":
        return {"type": "boolean"}
    if kind == "list":
        return {"type": "array", "items": {"type": "string"}}
    return {"type": ["string", "null"]}          # str | id | date | text_raw


def output_schema() -> dict:
    """JSON Schema generado desde el esquema v2: fuerza la estructura exacta."""
    return {
        "type": "object",
        "properties": {f: _json_type(FIELD_KINDS[f], f) for f in FIELD_ORDER},
        "required": list(FIELD_ORDER),
    }


# --------------------------------------------------------------- rendering

def _render_pages(data: bytes, config: GemmaConfig) -> list[str]:
    """PDF -> PNGs en base64 (pypdfium2, ya presente via pdfplumber)."""
    import pypdfium2 as pdfium  # type: ignore

    doc = pdfium.PdfDocument(io.BytesIO(data))
    images: list[str] = []
    try:
        for index, page in enumerate(doc):
            if index >= config.max_pages:
                break
            bitmap = page.render(scale=config.dpi / 72)
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="PNG")
            images.append(base64.b64encode(buffer.getvalue()).decode())
    finally:
        doc.close()
    return images


# ------------------------------------------------------------------ ollama

def _call_ollama(images: list[str], config: GemmaConfig) -> dict:
    prompt = build_extraction_prompt(
        "(Las paginas del documento se adjuntan como imagenes.)"
    )
    payload = {
        "model": config.model,
        "stream": False,
        "format": output_schema(),           # structured output: la respuesta ES el JSON
        "options": {"temperature": 0, "num_ctx": config.num_ctx},
        "messages": [{"role": "user", "content": prompt, "images": images}],
    }
    request = urllib.request.Request(
        f"{config.url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout) as response:
        body = json.loads(response.read())
    return json.loads(body["message"]["content"])


# ------------------------------------------------------------ normalizacion

def _normalize_amount(value: Any, *, rate: bool = False) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None
    if rate:
        return format(number.normalize(), "f")
    return f"{number.quantize(Decimal('0.01'))}"


def _normalize(raw: dict) -> dict:
    """Salida del modelo -> documento v2 estricto (claves y tipos exactos)."""
    doc = empty_document()
    for field in FIELD_ORDER:
        value = raw.get(field)
        if isinstance(value, str) and not value.strip():
            value = None
        doc[field] = value

    for field in _AMOUNT_FIELDS:
        doc[field] = _normalize_amount(doc[field], rate=field == "tipo_iva")
    doc["iban_enmascarado"] = bool(doc.get("iban_enmascarado"))
    ilegibles = doc.get("campos_ilegibles")
    doc["campos_ilegibles"] = [str(v) for v in ilegibles] if isinstance(ilegibles, list) else []
    for field in ("proveedor_tax_id", "cliente_tax_id", "iban", "bic"):
        if isinstance(doc.get(field), str):
            doc[field] = doc[field].replace(" ", "").replace("-", "").upper() or None

    # Vencimiento calculable: implementacion de referencia del esquema
    if doc.get("fecha_vencimiento_calculada") is None and doc.get("fecha_vencimiento_texto"):
        try:
            from datetime import date
            emision = date.fromisoformat(doc.get("fecha_emision") or "")
            computed = compute_due_date(doc["fecha_vencimiento_texto"], emision)
            if computed is not None:
                doc["fecha_vencimiento_calculada"] = computed.isoformat()
        except ValueError:
            pass
    return doc


# ---------------------------------------------------------------- adapter

def extract_with_gemma(filename: str, data: bytes, config: GemmaConfig) -> PocResult:
    """Extrae un documento con Gemma 4 y lo valida contra el contrato v2."""
    local_pdf = read_pdf_bytes(filename, data)
    images = _render_pages(data, config)
    if not images:
        raise RuntimeError("no se pudo renderizar ninguna pagina del PDF")

    doc = _normalize(_call_ollama(images, config))

    # El texto vectorial local preserva referencias que la vision puede perder
    local_doc = extract_document(local_pdf).document
    for field in ("po_reference", "project_reference"):
        if doc.get(field) in (None, "") and local_doc.get(field):
            doc[field] = local_doc[field]

    confidences: dict[str, Decimal] = {}
    dudosos = set(doc["campos_ilegibles"])
    for field in FIELD_ORDER:
        if doc.get(field) in (None, "", []):
            continue
        confidences[field] = Decimal("0.40") if field in dudosos else Decimal("0.90")

    warnings = _validate_invoice(doc, local_pdf.text, confidences)
    return PocResult(
        doc_id=Path(filename).stem,
        archivo=filename,
        pages=local_pdf.pages,
        text_chars=len(local_pdf.text),
        confidence=_result_confidence(doc, confidences),
        warnings=warnings,
        document=doc,
        engine=ENGINE_NAME,
        field_confidences=confidences,
    )


# ----------------------------------------------------------------- router

def extract_uploaded_document(filename: str, data: bytes) -> PocResult:
    """Punto de entrada de la UI: Gemma 4 primero, Document AI como fallback.

    Orden de decision:
      1. Gemma 4 local (costo cero). Si el resultado valida limpio, listo.
      2. Si Gemma advierte problemas y Document AI esta configurado, se paga
         UNA llamada solo para ese documento y se queda el mejor resultado.
      3. Si Gemma no esta disponible, se conserva el flujo anterior completo
         (Document AI o extractor local degradado).
    """
    config = GemmaConfig.from_env()
    if config is None:
        return _document_ai_flow(filename, data)

    try:
        gemma_result = extract_with_gemma(filename, data, config)
    except Exception as exc:
        fallback = _document_ai_flow(filename, data)
        fallback.warnings.insert(
            0, f"Gemma no disponible ({type(exc).__name__}); se uso el flujo anterior"
        )
        return fallback

    clean = not gemma_result.warnings
    is_invoice = gemma_result.document.get("document_type") == "invoice"
    if clean or not is_invoice or not is_document_ai_configured():
        return gemma_result

    try:
        managed = _document_ai_flow(filename, data)
    except Exception:
        return gemma_result
    if (len(managed.warnings), -managed.confidence) < (len(gemma_result.warnings), -gemma_result.confidence):
        managed.warnings.insert(0, "fallback pago: Gemma no valido limpio este documento")
        return managed
    return gemma_result
