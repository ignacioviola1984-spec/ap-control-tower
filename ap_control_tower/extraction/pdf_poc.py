"""PoC local para procesar PDFs reales contra el contrato de extraccion v2.

No llama APIs externas y no escribe material real fuera de ``runs/`` salvo que
se indique explicitamente otro output. Es un harness de lectura, clasificacion
y extraccion heuristica: sirve para ver rapido que entiende el sistema y para
preparar un etiquetado humano comparable con ``comparator.py``.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import unicodedata
from io import BytesIO
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .banking import bank_evidence_present, extract_bank_details
from .comparator import labels_template_row
from .historical_fields import extract_historical_fields
from .schema import FIELD_ORDER, compute_due_date, empty_document, validate_document


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "docs" / "poc-real"
DEFAULT_RUNS = ROOT / "runs" / "poc-real"


LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:S\.?\s*L\.?\s*U?\.?|S\.?\s*A\.?|B\.?\s*V\.?|S\.?\s*A\.?\s*S\.?|"
    r"SASU|LTD\.?|LIMITED|SARL|GMBH|INC\.?|LLC)\b",
    re.IGNORECASE,
)
VAT_PREFIX = (
    "AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|GB|GR|HR|HU|IE|IT|LT|LU|LV|MT|"
    "NL|NO|PL|PT|RO|SE|SI|SK|CH"
)
TAX_ID_RE = re.compile(
    rf"\b(?:(?:{VAT_PREFIX})(?=[A-Z0-9]{{8,14}}\b)(?=[A-Z0-9]*\d)[A-Z0-9]{{8,14}}|"
    rf"[ABCDEFGHJKLMNPQRSUVW]\d{{7}}[0-9A-J])\b",
    re.IGNORECASE,
)
MONTHS = {
    "jan": 1, "january": 1, "enero": 1, "janvier": 1,
    "feb": 2, "february": 2, "febrero": 2, "fevrier": 2, "fevrier": 2,
    "mar": 3, "march": 3, "marzo": 3, "mars": 3,
    "apr": 4, "april": 4, "abril": 4, "avril": 4,
    "may": 5, "mayo": 5, "mai": 5,
    "jun": 6, "june": 6, "junio": 6, "juin": 6,
    "jul": 7, "july": 7, "julio": 7, "juillet": 7,
    "aug": 8, "august": 8, "agosto": 8, "aout": 8, "août": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9, "septembre": 9,
    "oct": 10, "october": 10, "octubre": 10, "octobre": 10,
    "nov": 11, "november": 11, "noviembre": 11, "novembre": 11,
    "dec": 12, "december": 12, "diciembre": 12, "decembre": 12, "décembre": 12,
}


@dataclass
class PdfText:
    path: Path
    pages: int
    text: str


@dataclass
class PocResult:
    doc_id: str
    archivo: str
    pages: int
    text_chars: int
    confidence: Decimal
    warnings: list[str]
    document: dict[str, Any]
    engine: str = "local"
    field_confidences: dict[str, Decimal] = field(default_factory=dict)
    #: Texto extraído del PDF, retenido en memoria para que el asistente pueda
    #: responder sobre el contenido. El PDF binario nunca se envía a OpenAI.
    source_text: str = ""
    #: Vínculo con el maestro de proveedores (forma segura, sin IBAN en claro).
    #: Lo completa la sesión al aplicar el maestro; la política de revisión lo
    #: lee desde acá para no tener que arrastrarlo por cuatro firmas distintas.
    supplier_resolution: dict[str, Any] | None = None


def _fold(s: str) -> str:
    base = unicodedata.normalize("NFKD", s.replace("\x00", " "))
    return "".join(ch for ch in base if not unicodedata.combining(ch)).casefold()


def _clean_text(text: str) -> str:
    return (
        text.replace("\x00", " ")
        .replace("\xa0", " ")
        .replace("\u202f", " ")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_pdf_text(path: Path) -> PdfText:
    """Extrae texto con pdfplumber; usa pypdf como fallback."""
    try:
        import pdfplumber  # type: ignore

        chunks: list[str] = []
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
            for page in pdf.pages:
                chunks.append(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
        return PdfText(path=path, pages=pages, text=_clean_text("\n\n".join(chunks)))
    except Exception:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:  # pragma: no cover - depende del entorno local
            raise RuntimeError(
                "Faltan librerias para leer PDFs. Instalar: pip install pdfplumber pypdf"
            ) from exc
        reader = PdfReader(str(path))
        chunks = [page.extract_text() or "" for page in reader.pages]
        return PdfText(path=path, pages=len(reader.pages), text=_clean_text("\n\n".join(chunks)))


def read_pdf_bytes(filename: str, data: bytes) -> PdfText:
    """Extrae texto desde un PDF subido por la UI, sin persistir el archivo."""
    try:
        import pdfplumber  # type: ignore

        chunks: list[str] = []
        with pdfplumber.open(BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            for page in pdf.pages:
                chunks.append(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
        return PdfText(path=Path(filename), pages=pages, text=_clean_text("\n\n".join(chunks)))
    except Exception:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:  # pragma: no cover - depende del entorno local
            raise RuntimeError(
                "Faltan librerias para leer PDFs. Instalar: pip install pdfplumber pypdf"
            ) from exc
        reader = PdfReader(BytesIO(data))
        chunks = [page.extract_text() or "" for page in reader.pages]
        return PdfText(path=Path(filename), pages=len(reader.pages), text=_clean_text("\n\n".join(chunks)))


def _parse_date_value(raw: str) -> date | None:
    raw = raw.strip()
    iso = re.search(r"\b(20\d{2})[-/.]([01]?\d)[-/.]([0-3]?\d)\b", raw)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    numeric = re.search(r"\b([0-3]?\d)[-/.]([01]?\d)[-/.]((?:20)?\d{2})\b", raw)
    if numeric:
        year = int(numeric.group(3))
        if year < 100:
            year += 2000
        return _safe_date(year, int(numeric.group(2)), int(numeric.group(1)))

    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))
    textual = re.search(
        rf"\b([0-3]?\d)(?:st|nd|rd|th)?\s+(?:de\s+)?({month_words})\s+(?:de\s+)?(20\d{{2}})\b",
        _fold(raw),
    )
    if textual:
        return _safe_date(int(textual.group(3)), MONTHS[textual.group(2)], int(textual.group(1)))
    textual_us = re.search(
        rf"\b({month_words})\s+([0-3]?\d)(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b",
        _fold(raw),
    )
    if textual_us:
        return _safe_date(int(textual_us.group(3)), MONTHS[textual_us.group(1)], int(textual_us.group(2)))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _all_dates(raw: str) -> list[date]:
    dates: list[date] = []
    for m in re.finditer(r"\b(?:20\d{2}[-/.][01]?\d[-/.][0-3]?\d|[0-3]?\d[-/.][01]?\d[-/.](?:20)?\d{2})\b", raw):
        d = _parse_date_value(m.group(0))
        if d:
            dates.append(d)
    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))
    for m in re.finditer(
        rf"\b[0-3]?\d(?:st|nd|rd|th)?\s+(?:de\s+)?(?:{month_words})\s+(?:de\s+)?20\d{{2}}\b",
        _fold(raw),
    ):
        d = _parse_date_value(m.group(0))
        if d:
            dates.append(d)
    for m in re.finditer(rf"\b(?:{month_words})\s+[0-3]?\d(?:st|nd|rd|th)?[,]?\s+20\d{{2}}\b", _fold(raw)):
        d = _parse_date_value(m.group(0))
        if d:
            dates.append(d)
    return dates


#: Etiquetas de cabecera que suelen apilarse antes del bloque de valores.
_HEADER_LABEL_RE = re.compile(
    r"(?i)^\s*(?:invoice\s+number|purchase\s+order|emission\s+date|expiring\s+date|"
    r"expiry\s+date|expiration\s+date|due\s+date|invoice\s+date|numero\s+de\s+factura|"
    r"fecha|vencimiento)\s*:?\s*$"
)


def _find_date_after(lines: list[str], labels: tuple[str, ...]) -> date | None:
    for i, line in enumerate(lines):
        folded = _fold(line)
        if not any(label in folded for label in labels):
            continue
        # Layout de cabeceras apiladas (Lua Group): todas las etiquetas van
        # seguidas y después el bloque de valores. Leer la primera fecha tras la
        # etiqueta devolvía la de emisión; hay que respetar la posición de la
        # etiqueta dentro del grupo.
        if _HEADER_LABEL_RE.match(line):
            group_start = i
            while group_start > 0 and _HEADER_LABEL_RE.match(lines[group_start - 1]):
                group_start -= 1
            group_end = i
            while group_end + 1 < len(lines) and _HEADER_LABEL_RE.match(lines[group_end + 1]):
                group_end += 1
            if group_end > group_start:
                values = lines[group_end + 1:group_end + 2 + (group_end - group_start) * 2]
                dates = _all_dates(" ".join(values))
                # Las etiquetas sin valor (p. ej. "Purchase order" vacío) hacen
                # que no haya correspondencia 1 a 1; se toma la última fecha del
                # bloque, que es la del vencimiento por ser la etiqueta final.
                if dates and i == group_end:
                    return dates[-1]
                if dates:
                    return dates[0]
        window = " ".join(lines[i:i + 3])
        dates = _all_dates(window)
        if dates:
            return dates[0]
    return None


def _decimal(raw: str) -> Decimal | None:
    s = re.sub(r"(?i)\b(?:eur|usd|gbp)\b|€|\$", "", raw).strip()
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "." in s and len(s.rsplit(".", 1)[1]) == 3:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _format_decimal(value: Decimal | None) -> str | None:
    return f"{value.quantize(Decimal('0.01'))}" if value is not None else None


AMOUNT_RE = re.compile(
    r"(?:(?:EUR|USD|GBP)\s*)?-?\d{1,3}(?:,\d{3})+\.\d{2,4}"
    r"|(?:(?:EUR|USD|GBP)\s*)?-?\d{1,3}(?:[ .]\d{3})*(?:[.,]\d{2,4})"
    r"|-?\d+(?:[.,]\d{2,4})(?:\s*(?:EUR|USD|GBP|€))?"
    r"|-?\d{1,3}(?:[ .]\d{3})+(?:\s*(?:EUR|USD|GBP|€))?",
    re.IGNORECASE,
)


def _amounts(line: str) -> list[Decimal]:
    # Algunos PDFs separan visualmente el millar como ``1 .320,00``. Quitar
    # solo el espacio entre un digito y el separador conserva la evidencia y
    # evita degradar el importe a 320,00.
    normalized = re.sub(r"(?<=\d)\s+(?=[.,]\d)", "", line)
    return [v for v in (_decimal(m.group(0)) for m in AMOUNT_RE.finditer(normalized)) if v is not None]


def _find_amount(lines: list[str], labels: tuple[str, ...], reject: tuple[str, ...] = ()) -> Decimal | None:
    candidates: list[Decimal] = []
    for line in lines:
        folded = _fold(line)
        if any(label in folded for label in labels) and not any(bad in folded for bad in reject):
            vals = _amounts(line)
            if vals:
                candidates.append(vals[-1])
    return candidates[-1] if candidates else None


def _first_regex(patterns: tuple[str, ...], text: str, flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        m = re.search(pattern, text, flags)
        if m:
            return m.group(1).strip(" :#-")
    return None


def _split_client_supplier(line: str) -> tuple[str | None, str | None]:
    matches = list(LEGAL_SUFFIX_RE.finditer(line))
    if len(matches) >= 2:
        client = line[:matches[0].end()].strip(" -|")
        supplier = line[matches[0].end():matches[1].end()].strip(" -|")
        return client or None, supplier or None
    return None, None


def _legal_entities(lines: list[str]) -> list[str]:
    entities: list[str] = []
    for line in lines:
        if LEGAL_SUFFIX_RE.search(line):
            client, supplier = _split_client_supplier(line)
            if client and supplier:
                entities.extend([client, supplier])
            else:
                cleaned = re.sub(r"\s{2,}", " ", line).strip(" -|")
                if len(cleaned) <= 90:
                    entities.append(cleaned)
    seen: set[str] = set()
    unique: list[str] = []
    for entity in entities:
        key = _fold(entity)
        if key not in seen:
            seen.add(key)
            unique.append(entity)
    return unique


def _extract_names(doc: dict, lines: list[str], text: str) -> None:
    if doc["document_type"] == "other":
        labelled_supplier = _first_regex((
            r"(?im)^\s*Proveedor\s+Nombre\s*:\s*([^\n]+)$",
            r"(?im)^\s*Proveedor\s*:\s*([^\n]+)$",
        ), text)
        if labelled_supplier:
            doc["proveedor_nombre_comercial"] = labelled_supplier

    for i, line in enumerate(lines[:15]):
        if "client supplier" in _fold(line) and i + 1 < len(lines):
            client, supplier = _split_client_supplier(lines[i + 1])
            if client:
                doc["cliente_nombre"] = client
            if supplier:
                doc["proveedor_razon_social_legal"] = supplier
                doc["proveedor_nombre_comercial"] = supplier
            break

    for i, line in enumerate(lines):
        folded = _fold(line)
        if doc["cliente_nombre"] is None and any(x in folded for x in ("bill to", "cliente", "client:", "customer", "para:")):
            value = re.sub(r"(?i)^(bill to|cliente|client|customer|para)\s*[:#-]?\s*", "", line).strip()
            if value and len(value) < 100:
                doc["cliente_nombre"] = value

    entities = _legal_entities(lines)
    if doc["proveedor_razon_social_legal"] is None:
        if "client supplier" in _fold(text) and len(entities) >= 2:
            doc["cliente_nombre"] = doc["cliente_nombre"] or entities[0]
            doc["proveedor_razon_social_legal"] = entities[1]
        elif doc["document_type"] == "proforma_or_advance_request" and len(entities) >= 2:
            doc["cliente_nombre"] = doc["cliente_nombre"] or entities[0]
            doc["proveedor_razon_social_legal"] = entities[-1]
        elif entities:
            doc["proveedor_razon_social_legal"] = entities[0]
    if doc["proveedor_nombre_comercial"] is None and doc["proveedor_razon_social_legal"]:
        doc["proveedor_nombre_comercial"] = doc["proveedor_razon_social_legal"]


def _extract_tax_ids(doc: dict, lines: list[str], text: str) -> None:
    labelled: list[str] = []
    for line in lines:
        folded = _fold(line)
        if any(label in folded for label in ("cif", "nif", "vat", "tin", "tax id", "tva", "nuestro cif", "vuestro cif")):
            labelled.extend(m.group(0).upper().replace(" ", "") for m in TAX_ID_RE.finditer(line))
    ids = labelled or [m.group(0).upper().replace(" ", "") for m in TAX_ID_RE.finditer(text)]
    ids = [v for i, v in enumerate(ids) if v not in ids[:i]]
    if "CLIENT SUPPLIER" in text.upper() and len(ids) >= 2:
        doc["cliente_tax_id"] = ids[0]
        doc["proveedor_tax_id"] = ids[-1]
    else:
        if ids:
            doc["proveedor_tax_id"] = ids[0]
        if len(ids) >= 2:
            doc["cliente_tax_id"] = ids[1]

    for line in lines:
        folded = _fold(line)
        if any(label in folded for label in ("tva intracommunautaire", "nuestro cif")):
            found = [m.group(0).upper().replace(" ", "") for m in TAX_ID_RE.finditer(line)]
            if found:
                doc["proveedor_tax_id"] = found[-1]

    historical = extract_historical_fields(text)
    if historical.proveedor_registro:
        doc["proveedor_registro"] = historical.proveedor_registro.value


def _classify(text: str, filename: str) -> str:
    folded = _fold(f"{filename}\n{text}")
    top = _fold("\n".join(_lines(text)[:6]))
    invoice_terms = ("invoice", "factura", "facture", "rechnung")
    po_terms = ("orden de compra", "purchase order", "pedido de compra", "bon de commande")
    proforma_terms = ("proforma", "anticipo", "advance request", "solicitud de anticipo", "presupuesto", "quotation", "quote")

    if any(term in _fold(filename) for term in po_terms) or any(term in top for term in po_terms):
        return "other"
    if any(term in folded for term in proforma_terms):
        return "proforma_or_advance_request"
    if re.search(r"(?im)^\s*F\d{2,5}\s*$", text) and len(_amounts(text)) >= 3:
        return "invoice"
    if any(term in folded for term in invoice_terms):
        return "invoice"
    if any(term in folded for term in po_terms):
        return "other"
    return "other"


def _extract_document_number(doc: dict, text: str) -> None:
    if doc["document_type"] != "invoice":
        return
    lines = _lines(text)
    for i, line in enumerate(lines):
        folded = _fold(line)
        if "numero factura" in folded and "fecha" in folded and i + 1 < len(lines):
            first = lines[i + 1].split()[0].strip()
            if re.search(r"[A-Z0-9]", first):
                doc["numero_factura"] = first
                return

    for line in lines:
        folded = _fold(line)
        value = _first_regex((
            r"\bN[úu]mero\s+de\s+factura\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})",
            r"\bInvoice\s*N[°ºo.]?\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
            r"\bInvoice\s*(?:number|no\.?|n[ºo.]*)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
            r"\bFactura\s*(?:n[úu]m(?:ero)?\.?|n[ºo.]*)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})",
            r"\bFacture\s*(?:n[ºo.]*)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
        ), line)
        if value and _fold(value) not in {"date", "fecha", "client", "supplier"}:
            doc["numero_factura"] = value
            return
        if any(word in folded for word in ("factura", "invoice", "facture")):
            generic = _first_regex((
                r"\bN[ºo.]+\s*(?:factura|invoice|facture)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})",
            ), line)
            if generic:
                doc["numero_factura"] = generic
                return

    first_line = lines[0] if lines else ""
    value = _first_regex((r"^\s*(F\d{2,5})\s*$",), first_line)
    if value:
        doc["numero_factura"] = value


def _extract_dates(doc: dict, lines: list[str], text: str) -> None:
    for i, line in enumerate(lines):
        folded = _fold(line)
        if "numero factura" in folded and "fecha" in folded and i + 1 < len(lines):
            dates = _all_dates(lines[i + 1])
            if dates:
                doc["fecha_emision"] = dates[0].isoformat()
                break

    for i, line in enumerate(lines):
        folded = _fold(line)
        if "invoice date" in folded and "date of service" in folded and "payment due date" in folded and i + 1 < len(lines):
            dates = _all_dates(lines[i + 1])
            if len(dates) >= 3:
                doc["fecha_emision"] = dates[0].isoformat()
                doc["periodo_servicio_desde"] = dates[1].isoformat()
                doc["periodo_servicio_hasta"] = dates[1].isoformat()
                doc["fecha_vencimiento_texto"] = lines[i + 1].strip()
                doc["fecha_vencimiento_calculada"] = dates[2].isoformat()
                return

    issued = _find_date_after(lines, (
        "invoice date", "fecha de emision", "fecha emision", "fecha de la factura",
        "date facture", "date of issue", "fecha:", "factura fecha",
    ))
    due = _find_date_after(lines, ("payment due date", "due date", "vencimiento",
                                   "fecha de vencimiento", "echeance",
                                   # Lua Group etiqueta el vencimiento como
                                   # "Expiring date" y se perdía por completo.
                                   "expiring date", "expiry date", "expiration date"))
    if issued:
        doc["fecha_emision"] = issued.isoformat()
    elif doc["document_type"] == "invoice" and doc["numero_factura"]:
        # Algunos PDFs vectoriales conservan los valores pero pierden las etiquetas.
        # La fecha inmediatamente posterior al numero fiscal sigue siendo evidencia visible.
        invoice_position = next(
            (i for i, line in enumerate(lines) if doc["numero_factura"] in line),
            None,
        )
        if invoice_position is not None:
            nearby = _all_dates(" ".join(lines[invoice_position:invoice_position + 3]))
            if nearby:
                doc["fecha_emision"] = nearby[0].isoformat()
    if due:
        doc["fecha_vencimiento_texto"] = due.isoformat()
        doc["fecha_vencimiento_calculada"] = due.isoformat()

    historical = extract_historical_fields(text)
    if historical.condiciones_pago:
        terms = historical.condiciones_pago.value
        doc["condiciones_pago"] = terms
        if doc["fecha_vencimiento_texto"] is None:
            days = re.search(
                r"\b\d{1,3}\s*(?:days?|dias|d[ií]as)(?:\s*(?:end of month|fin de mes|f\.?f\.?))?\b",
                _fold(terms),
            )
            if days:
                doc["fecha_vencimiento_texto"] = days.group(0)
                if doc["fecha_emision"]:
                    calculated = compute_due_date(days.group(0), date.fromisoformat(doc["fecha_emision"]))
                    doc["fecha_vencimiento_calculada"] = calculated.isoformat() if calculated else None
    if historical.periodo_servicio_desde and historical.periodo_servicio_hasta:
        doc["periodo_servicio_desde"] = historical.periodo_servicio_desde.value
        doc["periodo_servicio_hasta"] = historical.periodo_servicio_hasta.value


def _extract_amounts(doc: dict, lines: list[str], text: str) -> None:
    folded = _fold(text)
    if "eur" in folded or "€" in text:
        doc["moneda"] = "EUR"
    elif "usd" in folded or "$" in text:
        doc["moneda"] = "USD"
    elif "gbp" in folded or "£" in text:
        doc["moneda"] = "GBP"

    for i, line in enumerate(lines):
        line_folded = _fold(line)
        if all(label in line_folded for label in ("base imponible", "impuesto", "total")) and i + 1 < len(lines):
            vals = _amounts(lines[i + 1])
            if len(vals) >= 3:
                doc["importe_neto"] = _format_decimal(vals[-3])
                doc["importe_iva"] = _format_decimal(vals[-2])
                doc["importe_total"] = _format_decimal(vals[-1])
                break

    doc["importe_neto"] = doc["importe_neto"] or _format_decimal(_find_amount(
        lines,
        ("total excl", "net amount", "base imponible", "subtotal", "total ht", "importe neto"),
        reject=("invoice total", "importe total", "total ttc"),
    ))
    doc["importe_iva"] = doc["importe_iva"] or _format_decimal(_find_amount(
        lines,
        ("vat amount", "vat", "iva", "tva", "importe iva", "impuesto"),
        reject=("total including vat", "total incluido", "total general", "invoice total"),
    ))
    total = _find_amount(
        lines,
        ("invoice total", "total", "importe total", "total ttc", "total including vat", "total factura", "amount due", "a pagar", "total general", "importe adeudado"),
        reject=("total excl", "vat amount", "net amount", "base imponible", "subtotal", "total ht"),
    )
    doc["importe_total"] = doc["importe_total"] or _format_decimal(total)
    if doc["document_type"] == "invoice" and doc["importe_total"] is None:
        vals = _amounts(text)
        if len(vals) >= 3:
            doc["importe_neto"] = doc["importe_neto"] or _format_decimal(vals[-3])
            doc["importe_iva"] = doc["importe_iva"] or _format_decimal(vals[-2])
            doc["importe_total"] = _format_decimal(vals[-1])

    rate = re.search(
        r"(?i)\b(?:vat|iva|tva)(?:\s+rate)?\s*[:(]?\s*(\d{1,2}(?:[,.]\d+)?)\s*%",
        text,
    )
    if rate:
        doc["tipo_iva"] = rate.group(1).replace(",", ".")

    if any(term in folded for term in ("reverse charge", "inversion del sujeto pasivo", "inversión del sujeto pasivo", "article 196")):
        doc["tratamiento_iva"] = "intracomunitario_inversion_sujeto_pasivo"
    elif doc["tipo_iva"] is not None or doc["importe_iva"] is not None:
        doc["tratamiento_iva"] = "nacional"
    elif doc["document_type"] == "proforma_or_advance_request":
        doc["tratamiento_iva"] = "no_desglosado"
    elif any(term in folded for term in ("exempt", "exento", "exonerado")):
        doc["tratamiento_iva"] = "exento_otro"


def _extract_payment(doc: dict, text: str) -> None:
    if doc["document_type"] == "other":
        doc["metodo_pago"] = "no_indicado"
        return
    folded = _fold(text)
    bank = extract_bank_details(text)
    if re.search(r"\b(direct debit|domiciliacion|domiciliacion bancaria|domiciliado|sepa|cargo en cuenta)\b", folded):
        doc["metodo_pago"] = "domiciliacion_direct_debit"
    elif re.search(r"\b(tarjeta|card payment|credit card|visa|mastercard)\b", folded):
        doc["metodo_pago"] = "tarjeta"
    elif re.search(r"\b(transferencia|bank transfer|wire transfer)\b", folded):
        doc["metodo_pago"] = "transferencia"
    else:
        doc["metodo_pago"] = "no_indicado"

    doc["iban"] = bank.iban
    doc["iban_enmascarado"] = bank.iban_enmascarado
    doc["bic"] = bank.bic
    doc["proveedor_banco"] = bank.banco
    doc["proveedor_cuenta_bancaria"] = bank.cuenta


def _extract_references(doc: dict, text: str) -> None:
    po = _first_regex((
        r"\b(?:PO|P\.O\.|OC|Orden de Compra|Purchase Order|Pedido)\s*(?:number|no\.?|n[ºo.]*)?\s*[:#-]?\s*([A-Z]{0,5}[- ]?\d{3,}[A-Z0-9-]*)",
        r"\b(?:Orden de Compra|Purchase Order)\s+([A-Z]{2,5}\s*\d{3,}[A-Z0-9-]*)",
        # Formato de referencia de pedido observado en run1 (2026-07-14):
        # letras + numero/anio, p. ej. ABC07/2026. Las referencias ORD-… de
        # contrato de proyecto NO van acá: son project_reference
        # (ver test_contract_project_reference_is_not_promoted_to_po).
        r"\b(?:Ref(?:erencia)?\.?|Nuestra ref\.?)\s*[:#-]?\s*([A-Z]{2,6}\s?\d{1,4}/\d{2,4})",
    ), text)
    if po:
        doc["po_reference"] = re.sub(r"\s+", " ", po).strip()

    project = _first_regex((
        r"\b(?:Order ref|Referencia(?: de estudio)?|Project|Proyecto|Contrato(?: del proyecto)?)\s*[:#-]?\s*([A-Z]{2,}[- ]?\d{3,}[A-Z0-9-]*)",
    ), text)
    if project and doc["po_reference"] != project:
        doc["project_reference"] = re.sub(r"\s+", " ", project).strip()


def _confidence(doc: dict) -> Decimal:
    if doc["document_type"] == "invoice":
        keys = (
            "document_type", "numero_factura", "fecha_emision",
            "proveedor_nombre_comercial", "cliente_nombre", "importe_neto",
            "tipo_iva", "importe_iva", "importe_total", "moneda",
            "tratamiento_iva", "metodo_pago",
        )
    elif doc["document_type"] == "proforma_or_advance_request":
        keys = ("document_type", "fecha_emision", "proveedor_nombre_comercial",
                "importe_total", "moneda", "tratamiento_iva", "metodo_pago")
    else:
        keys = ("document_type", "proveedor_nombre_comercial", "po_reference")
    score = sum(1 for key in keys if doc.get(key) not in (None, "", [])) / len(keys)
    return Decimal(str(score)).quantize(Decimal("0.01"))


def extract_document(pdf: PdfText) -> PocResult:
    doc = empty_document()
    text = pdf.text
    lines = _lines(text)
    warnings: list[str] = []

    doc["document_type"] = _classify(text, pdf.path.name)
    _extract_names(doc, lines, text)
    _extract_tax_ids(doc, lines, text)
    _extract_document_number(doc, text)
    _extract_dates(doc, lines, text)
    _extract_amounts(doc, lines, text)
    _extract_payment(doc, text)
    _extract_references(doc, text)

    if len(text.strip()) < 80:
        warnings.append("texto extraido muy bajo; probablemente requiere OCR")
    if doc["document_type"] == "invoice" and not doc["numero_factura"]:
        warnings.append("clasificada como invoice pero no se encontro numero_factura")
    if doc["document_type"] == "invoice":
        critical = (
            "fecha_emision", "proveedor_nombre_comercial", "cliente_nombre",
            "importe_neto", "tipo_iva", "importe_iva", "importe_total", "moneda",
        )
        missing = [name for name in critical if doc.get(name) in (None, "")]
        if missing:
            warnings.append("campos criticos ausentes: " + ", ".join(missing))
        if bank_evidence_present(text) and not any((
            doc.get("iban"), doc.get("bic"), doc.get("proveedor_banco"),
            doc.get("proveedor_cuenta_bancaria"),
        )):
            warnings.append("hay datos bancarios visibles pero no se pudieron estructurar")
    if doc["document_type"] == "other" and not doc["po_reference"]:
        warnings.append("clasificada como other; revisar si es OC u otro soporte")
    warnings.extend(validate_document(doc))

    return PocResult(
        doc_id=pdf.path.stem,
        archivo=str(pdf.path),
        pages=pdf.pages,
        text_chars=len(text),
        confidence=_confidence(doc),
        warnings=warnings,
        document=doc,
    )


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    return str(value)


def write_outputs(results: list[PocResult], texts: dict[str, str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    text_dir = output_dir / "doc_texts"
    text_dir.mkdir(exist_ok=True)
    for doc_id, text in texts.items():
        (text_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")

    with open(output_dir / "extracted_documents.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["doc_id", "archivo", *FIELD_ORDER, "confidence", "pages", "text_chars", "warnings"])
        for r in results:
            writer.writerow([
                r.doc_id, r.archivo, *[_csv_cell(r.document[f]) for f in FIELD_ORDER],
                str(r.confidence), r.pages, r.text_chars, " | ".join(r.warnings),
            ])

    with open(output_dir / "review_labels_template.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(labels_template_row())
        for r in results:
            writer.writerow([
                r.doc_id, r.archivo, *[_csv_cell(r.document[f]) for f in FIELD_ORDER],
                "REVISAR: corregir campos para convertir en golden label humano",
            ])

    with open(output_dir / "summary.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "doc_id", "archivo", "document_type", "confidence", "numero_factura",
            "po_reference", "proveedor", "importe_total", "moneda", "metodo_pago",
            "pages", "text_chars", "warnings",
        ])
        for r in results:
            d = r.document
            writer.writerow([
                r.doc_id, Path(r.archivo).name, d["document_type"], str(r.confidence),
                d["numero_factura"], d["po_reference"], d["proveedor_nombre_comercial"],
                d["importe_total"], d["moneda"], d["metodo_pago"], r.pages, r.text_chars,
                " | ".join(r.warnings),
            ])

    payload = [
        {
            "doc_id": r.doc_id,
            "archivo": r.archivo,
            "pages": r.pages,
            "text_chars": r.text_chars,
            "confidence": str(r.confidence),
            "warnings": r.warnings,
            "document": r.document,
        }
        for r in results
    ]
    (output_dir / "extracted_documents.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (output_dir / "README.txt").write_text(
        "PoC local de PDFs reales AP Control Tower\n"
        "Archivos:\n"
        "- summary.csv: vista ejecutiva por documento.\n"
        "- extracted_documents.csv/json: extraccion completa segun esquema v2.\n"
        "- review_labels_template.csv: copia editable para etiquetado humano.\n"
        "- doc_texts/: texto extraido de cada PDF, para diagnostico/OCR.\n\n"
        "Todo este directorio queda bajo runs/ y no se commitea.\n",
        encoding="utf-8",
    )


def run_poc(input_dir: Path = DEFAULT_INPUT, output_dir: Path | None = None) -> Path:
    by_key = {p.resolve().as_posix().casefold(): p for p in [*input_dir.glob("*.pdf"), *input_dir.glob("*.PDF")]}
    pdfs = sorted(by_key.values())
    if not pdfs:
        raise FileNotFoundError(f"No encontre PDFs en {input_dir}")
    output_dir = output_dir or DEFAULT_RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")

    results: list[PocResult] = []
    texts: dict[str, str] = {}
    for pdf_path in pdfs:
        pdf = read_pdf_text(pdf_path)
        texts[pdf.path.stem] = pdf.text
        results.append(extract_document(pdf))
    write_outputs(results, texts, output_dir)
    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Procesa PDFs reales del PoC AP contra extraccion v2.")
    parser.add_argument("input_dir", nargs="?", default=str(DEFAULT_INPUT),
                        help="Carpeta con PDFs reales (default: docs/poc-real)")
    parser.add_argument("--output-dir", default=None,
                        help="Carpeta de salida (default: runs/poc-real/<timestamp>)")
    args = parser.parse_args(argv)

    out = run_poc(Path(args.input_dir), Path(args.output_dir) if args.output_dir else None)
    print(f"OK PoC PDF -> {out}")
    print(f"  summary: {out / 'summary.csv'}")
    print(f"  extraccion: {out / 'extracted_documents.csv'}")
    print(f"  template humano: {out / 'review_labels_template.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
