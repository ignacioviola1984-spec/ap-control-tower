"""Prompt de extraccion v2: generado desde el esquema (nunca driftea).

Este prompt mantiene el contrato de extraccion independiente del motor y sirve
como especificacion evaluable de los campos. La regla anti-alucinacion es
EXPLICITA y no negociable.
"""

from __future__ import annotations

from .schema import ENUM_VALUES, FIELD_NOTES, FIELD_ORDER

ANTI_HALLUCINATION_RULE = """REGLA ANTI-ALUCINACION (obligatoria, prevalece sobre todo lo demas):
- Un campo que NO es visible en el documento va en null. NUNCA se infiere,
  se deriva, se completa por conocimiento general ni se copia de otro campo.
- En particular: NUNCA inventar proveedor_razon_social_legal a partir del
  nombre comercial; NUNCA completar digitos de un IBAN enmascarado; NUNCA
  suponer un numero de factura en una proforma.
- Distinguir "no esta" de "esta pero ilegible": si el dato existe en el
  documento pero no se puede leer (borroso, cortado, tapado), el campo va en
  null Y el nombre del campo se agrega a campos_ilegibles.
- Ante duda entre dos lecturas posibles de un valor: null + campos_ilegibles.
  Un null correcto vale mas que un valor plausible incorrecto."""


def _fields_block() -> str:
    lines = []
    for f in FIELD_ORDER:
        enum_note = ""
        if f in ENUM_VALUES:
            enum_note = " Valores permitidos: " + " | ".join(f'"{v}"' for v in ENUM_VALUES[f]) + "."
        lines.append(f"- {f}: {FIELD_NOTES[f]}{enum_note}")
    return "\n".join(lines)


EXTRACTION_PROMPT_TEMPLATE = f"""Sos el agente de ingesta de AP Control Tower. Extraes datos estructurados de
documentos de proveedor (facturas, proformas, otros) para el proceso de
Cuentas a Pagar.

PASO 1 - CLASIFICAR EL DOCUMENTO (document_type, antes que cualquier campo):
- "invoice": factura fiscal. Tiene numero de factura y el IVA esta tratado
  (desglosado, a tipo 0 con regla explicita, o con mencion de exencion).
- "proforma_or_advance_request": presupuesto o solicitud de anticipo. Sin
  numero fiscal, sin IVA desglosado; suele mencionar una factura final futura.
- "other": cualquier otro documento (orden de compra, recibo, nota de entrega).
La clasificacion es parte del output evaluado: equivocar el tipo es un error.

PASO 2 - EXTRAER LOS CAMPOS (en este orden exacto):
{_fields_block()}

{ANTI_HALLUCINATION_RULE}

PRECISIONES:
- po_reference SOLO admite referencias etiquetadas explicitamente como
  PO / OC / Orden de Compra / Purchase Order / Pedido. Una referencia tipo
  "ORD-2026-114" u "Order ref" sin la palabra pedido/PO va en
  project_reference, no en po_reference.
- fecha_vencimiento_texto es SIEMPRE el texto crudo. La calculada solo se
  completa si el texto es aritmeticamente calculable desde la emision
  (ej. "45 days end of month" = fin del mes de emision + 45 dias). Textos
  como "al inicio del estudio" no son calculables: calculada = null.
- Periodos tipo "cuota ABRIL 2026" se estructuran como
  periodo_servicio_desde=2026-04-01 y periodo_servicio_hasta=2026-04-30.
- Fechas en ISO YYYY-MM-DD. Importes con punto decimal, sin separador de miles.

SALIDA: un unico JSON con EXACTAMENTE estas claves:
{", ".join(FIELD_ORDER)}

DOCUMENTO A PROCESAR:
---
{{document_text}}
---"""


def build_extraction_prompt(document_text: str) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.replace("{document_text}", document_text)
