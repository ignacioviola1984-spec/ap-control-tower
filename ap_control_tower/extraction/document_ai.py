"""Google Document AI Invoice Parser adapter for uploaded PDFs.

The managed parser is the source of truth for invoice entities and layout.
The deterministic extractor remains useful for classification, references and
an explicit degraded-mode result when the managed service is unavailable.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from .banking import (
    bank_evidence_present,
    extract_bank_details,
    is_valid_bic,
    is_valid_iban,
    is_valid_spanish_ccc,
)
from .historical_fields import extract_historical_fields
from .pdf_poc import PdfText, PocResult, extract_document, read_pdf_bytes
from .schema import validate_document


ENGINE_NAME = "google_document_ai_invoice_parser"
INVOICE_CRITICAL_FIELDS = (
    "numero_factura",
    "fecha_emision",
    "proveedor_nombre_comercial",
    "cliente_nombre",
    "importe_neto",
    "tipo_iva",
    "importe_iva",
    "importe_total",
    "moneda",
    "tratamiento_iva",
)

LEGAL_SUFFIX_RE = re.compile(
    # SLP = sociedad limitada profesional, habitual en despachos (ELZABURU SLP).
    r"\b(?:S\.?\s*L\.?\s*(?:U|L|P)?\.?|S\.?\s*A\.?\s*U?\.?|B\.?\s*V\.?|"
    r"SASU|SAS|SARL|GMBH|LTD|LIMITED|LLC|DAC)\b",
    re.IGNORECASE,
)
TAX_ID_RE = re.compile(
    r"\b(?:(?:ES|FR|NL|DE|IT|PT|BE|AT|IE|GB)[A-Z0-9]{8,14}|[A-Z]-?\d{7,8}[A-Z0-9]?)\b",
    re.IGNORECASE,
)
COUNTRY_ALIASES = {
    "ES": {"es", "espana", "spain"},
    "FR": {"fr", "france"},
    "NL": {"nl", "netherlands", "the netherlands"},
    "DE": {"de", "germany", "deutschland"},
    "IT": {"it", "italy", "italia"},
    "PT": {"pt", "portugal"},
    "GB": {"gb", "uk", "united kingdom"},
}
DEFAULT_OWN_COMPANY_NAMES = (
    "Brand Up",
    "BMC Strategic Innovation Group",
    "BMC Estrategic Innovation Group",
    "Meridia Consulting",
)
DEFAULT_OWN_TAX_IDS = ("B85902583", "B86774718")
#: Permite reponer el nombre del cliente cuando el documento trae el CIF propio
#: pero no un nombre legible (p. ej. Telefónica escribe "Estimado cliente").
DEFAULT_OWN_PARTY_BY_TAX_ID = {
    "B85902583": "Brand Up",
    "B86774718": "BMC Strategic Innovation Group",
}
#: Nombres de persona física usados como contacto, no como razón social.
_PERSON_TITLE_RE = re.compile(
    r"^(?:mr|mrs|ms|miss|sr|sra|srta|dr|dra|don|dona|d\.|d\.ª)\b\.?\s+",
    re.IGNORECASE,
)


class NotInvoiceDocumentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentAIConfig:
    project_id: str
    location: str
    processor_id: str

    @classmethod
    def from_env(cls) -> "DocumentAIConfig | None":
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        location = os.getenv("DOCUMENT_AI_LOCATION", "us")
        processor = os.getenv("DOCUMENT_AI_PROCESSOR_ID")
        if not project or not processor:
            return None
        return cls(project_id=project, location=location, processor_id=processor)


def is_document_ai_configured() -> bool:
    return DocumentAIConfig.from_env() is not None


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


def _configured_values(env_name: str, defaults: tuple[str, ...]) -> list[str]:
    raw = os.getenv(env_name)
    values = raw.split(",") if raw is not None else defaults
    return [value.strip() for value in values if value.strip()]


def _is_own_party(value: str | None) -> bool:
    folded = _fold(value or "")
    return bool(folded) and any(
        _fold(name) in folded
        for name in _configured_values("AP_OWN_COMPANY_NAMES", DEFAULT_OWN_COMPANY_NAMES)
    )


#: Formatos plausibles de identificador fiscal. Evita que un número cualquiera
#: del documento (p. ej. el nº de transacción de Zoom) se cargue como CIF.
_PLAUSIBLE_TAX_ID_RE = re.compile(
    r"^(?:"
    r"[A-Z]\d{7}[A-Z0-9]"        # CIF español: letra + 7 dígitos + control
    r"|\d{8}[A-Z]"               # NIF español: 8 dígitos + letra
    r"|\d{9}"                    # VAT sin prefijo de país (p. ej. UK)
    r"|[A-Z]{2}[A-Z0-9]{8,12}"   # VAT intracomunitario con prefijo de país
    r")$"
)


def _is_plausible_tax_id(value: str | None) -> bool:
    compact = re.sub(r"\W", "", str(value or "")).upper().lstrip("0")
    return bool(compact) and bool(_PLAUSIBLE_TAX_ID_RE.match(compact))


def is_own_company(value: str | None) -> bool:
    """¿El nombre corresponde a una sociedad propia del grupo?

    API pública para que los controles del flujo puedan detectar facturas
    emitidas a un tercero sin depender de helpers internos.
    """
    return _is_own_party(value)


def _tax_id_key(value: str | None) -> str:
    compact = re.sub(r"\W", "", value or "").upper()
    if compact.startswith("ES") and len(compact) == 11:
        compact = compact[2:]
    # Algunos emisores (Telefónica) imprimen el CIF con ceros a la izquierda:
    # "00B85902583". Sin normalizarlos, el CIF propio no se reconoce y la
    # corrección emisor/receptor nunca se dispara. Se normaliza en ambos lados
    # de la comparación, así que es seguro.
    stripped = compact.lstrip("0")
    return stripped or compact


def _is_own_tax_id(value: str | None) -> bool:
    key = _tax_id_key(value)
    return bool(key) and key in {
        _tax_id_key(item)
        for item in _configured_values("AP_OWN_TAX_IDS", DEFAULT_OWN_TAX_IDS)
    }


#: Etiquetas que algunos emisores imprimen pegadas al número ("DNI12817931P").
#: Dejarlas dentro rompe la inferencia de país: "DN" no es un prefijo VAT y la
#: factura de una autónoma española quedaba clasificada como extracomunitaria.
_TAX_ID_LABEL_PREFIX_RE = re.compile(r"^(?:DNI|NIF|CIF|NIE|VAT|TIN|RUC|CUIT)(?=\d)")


def _clean_tax_id_candidate(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    compact = _TAX_ID_LABEL_PREFIX_RE.sub("", compact)
    if not (8 <= len(compact) <= 16) or not any(ch.isdigit() for ch in compact):
        return None
    # Evitar que el parser promueva un IBAN como tax ID.
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,}", compact):
        return None
    return compact


def _entity_confidence(entity: Any) -> Decimal:
    try:
        return Decimal(str(entity.confidence)).quantize(Decimal("0.01"))
    except (AttributeError, InvalidOperation):
        return Decimal("0.00")


def _entity_value(entity: Any) -> str | None:
    normalized = getattr(entity, "normalized_value", None)
    normalized_text = getattr(normalized, "text", None) if normalized else None
    if normalized_text:
        return str(normalized_text).strip()
    mention = getattr(entity, "mention_text", None)
    return str(mention).strip() if mention not in (None, "") else None


def _entity_mention(entity: Any) -> str | None:
    mention = getattr(entity, "mention_text", None)
    return str(mention).strip() if mention not in (None, "") else None


def _flatten_entities(entities: Iterable[Any]) -> list[Any]:
    flattened: list[Any] = []
    for entity in entities:
        flattened.append(entity)
        flattened.extend(_flatten_entities(getattr(entity, "properties", ()) or ()))
    return flattened


def _best_entities(document: Any) -> dict[str, Any]:
    best: dict[str, Any] = {}
    for entity in _flatten_entities(getattr(document, "entities", ()) or ()):
        entity_type = getattr(entity, "type_", None) or getattr(entity, "type", None)
        if not entity_type:
            continue
        current = best.get(entity_type)
        if current is None or _entity_confidence(entity) > _entity_confidence(current):
            best[entity_type] = entity
    return best


def _decimal_value(raw: str | None, *, rate: bool = False) -> str | None:
    if not raw:
        return None
    # (?![A-Z]) permite despegar la moneda pegada al número ("EUR105.98") sin
    # romper palabras como "EURO"; \b evita capturar dentro de otra palabra.
    value = re.sub(r"(?i)\b(?:EUR|USD|GBP|CHF)(?![A-Z])|[€$£%]", "", raw).strip()
    value = re.sub(r"\s+", "", value)
    if not value:
        return None
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif "." in value:
        # Solo puntos agrupando de a 3: separador de miles europeo
        # ("2.500" = dos mil quinientos, "1.234.567"), no decimales. Sin esto se
        # leía 2.500 como 2,50: un factor 1000 en el importe a pagar.
        if re.fullmatch(r"[-+]?\d{1,3}(?:\.\d{3})+", value):
            value = value.replace(".", "")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if rate:
        return format(number.normalize(), "f")
    return f"{number.quantize(Decimal('0.01'))}"


#: Fechas centinela que algunos ERP imprimen para decir "sin vencimiento".
_SENTINEL_DATES = {"9999-12-31", "0001-01-01", "1900-01-01"}
_SENTINEL_YEARS_RE = re.compile(r"\b(?:9999|0001|1900)\b")


def _looks_like_sentinel_date(raw: str) -> bool:
    return bool(_SENTINEL_YEARS_RE.search(raw))


def _date_value(raw: str | None) -> str | None:
    """Normaliza a ISO. Devuelve None si el texto no es una fecha.

    Antes se devolvía el texto crudo cuando no se reconocía una fecha, así que
    en un campo declarado como 'date' terminaban cadenas como "30 days from
    invoice issuance." o el centinela "31/12/9999". El texto libre de
    vencimiento tiene su propio campo (fecha_vencimiento_texto).
    """
    if not raw:
        return None
    value = raw.strip()
    match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", value)
    if not match:
        slashed = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", value)
        if not slashed:
            return None
        day, month, year = slashed.groups()
        iso = f"{year}-{int(month):02d}-{int(day):02d}"
    else:
        iso = match.group(0)
    return None if iso in _SENTINEL_DATES else iso


_CURRENCY_ALIASES = {
    "€": "EUR", "$": "USD", "£": "GBP",
    # OCR y abreviaturas del emisor: la factura de Iván Zimmermann escribe
    # "10.000,00 EU" y quedaba con moneda "EU", que no es un código ISO.
    "EU": "EUR", "EUR": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "USD": "USD", "GBP": "GBP", "US": "USD", "LIBRA": "GBP",
}


def _currency_value(raw: str | None) -> str | None:
    if not raw:
        return None
    folded = re.sub(r"[^A-Z€$£]", "", raw.strip().upper())
    if folded in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[folded]
    return folded[:3] if len(folded) >= 3 else None


def _clean_party_name(value: str) -> str:
    cleaned = re.sub(
        # "Sello" precede al cuño impreso junto a la razón social (Securitas).
        r"(?i)^\s*(?:supplier|client|cliente|nombre|account holder|address|sello)\s*[:#-]?\s*",
        "",
        value,
    )
    cleaned = re.split(
        r"(?i)\s+(?:fecha de la factura|invoice date|bank account|contacto cliente)\s*:",
        cleaned,
        maxsplit=1,
    )[0]
    cleaned = re.split(r"(?i)\s+-\s+(?:CIF|NIF|VAT|TIN)\b", cleaned, maxsplit=1)[0]
    return re.sub(r"\s+", " ", cleaned).strip(" |,;:-")


def _legal_name_candidates(text: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, line in enumerate(text.splitlines()):
        cleaned = _clean_party_name(line)
        folded = _fold(cleaned)
        if not cleaned or len(cleaned) > 180:
            continue
        if any(term in folded for term in (
            "bank account", "au capital", "registro mercantil", "conforme a la ley",
            "responsable de ventas", "director del proyecto",
        )):
            continue
        if cleaned.upper().startswith("SASU "):
            name = cleaned
        else:
            suffix = LEGAL_SUFFIX_RE.search(cleaned)
            if not suffix:
                continue
            name = cleaned[:suffix.end()].strip(" |,;:-")
        key = _fold(name)
        if key and key not in seen:
            seen.add(key)
            candidates.append((index, name))
    return candidates


def _labelled_party(text: str, label: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        folded = _fold(line).strip(" :#-")
        wanted = _fold(label)
        if folded == wanted:
            for candidate in lines[index + 1:index + 4]:
                cleaned = _clean_party_name(candidate)
                if cleaned and _fold(cleaned) not in {"client", "cliente", "supplier", "proveedor"}:
                    return cleaned
        if folded.startswith(wanted + ":"):
            value = _clean_party_name(line.split(":", 1)[1])
            if value:
                return value
    return None


#: Texto que aparece en facturas pero NUNCA es el nombre de una parte:
#: etiquetas de formulario, saludos, sellos y avisos legales.
_BAD_PARTY_TERMS = (
    "bank account", "account holder", "numero factura", "factura a", "titular:",
    "en cumplimiento", "accepts visa", "description", "vat no",
    "estimado cliente", "estimado/a", "dear customer", "dear client",
    "duplicado", "copia", "original", "plazo de pago", "forma de pago",
    "condiciones de pago", "datos bancarios", "numero de cuenta",
    "dirigir un escrito", "aviso legal", "registro mercantil",
    "domiciliacion", "recibo domiciliado", "periodo de facturacion",
)

#: Solo la forma jurídica, sin nombre (p. ej. "SAS" cuando la marca está en un logo).
_ONLY_LEGAL_FORM_RE = re.compile(
    r"^(?:S\.?\s*A\.?\s*S\.?\s*U?|S\.?\s*L\.?\s*U?|S\.?\s*A\.?|B\.?\s*V\.?|"
    r"LTD|LIMITED|GMBH|INC|LLC|SARL|SASU|N\.?\s*V\.?)\.?$",
    re.IGNORECASE,
)

#: Dominio o URL suelta (p. ej. "areaclientes.orange.es").
_URL_LIKE_RE = re.compile(r"^(?:https?://|www\.)|\.[a-z]{2,4}$", re.IGNORECASE)


def _is_bad_party_name(value: str | None) -> bool:
    if not value:
        return True
    cleaned = value.strip()
    folded = _fold(cleaned)
    if len(cleaned) > 120:
        return True
    if folded in {
        "grupo", "numero de cliente", "cliente", "client", "supplier", "proveedor",
        # Etiquetas del propio formulario tomadas como nombre: en el albarán de
        # Viñals el proveedor salía "N.I.F.".
        "n.i.f.", "n.i.f", "nif", "n.l.f.", "cif", "vat", "tax id", "factura",
        "invoice", "albaran", "fecha", "date", "total",
    }:
        return True
    if any(term in folded for term in _BAD_PARTY_TERMS):
        return True
    # Solo forma jurídica: no identifica a nadie.
    if _ONLY_LEGAL_FORM_RE.match(cleaned):
        return True
    # Persona de contacto ("Mrs Laura FEITO BARRIO"), no una razón social.
    if _PERSON_TITLE_RE.match(cleaned):
        return True
    # URL/dominio suelto (sin espacios, para no descartar razones sociales).
    if " " not in cleaned and _URL_LIKE_RE.search(cleaned):
        return True
    # Fragmento de frase: arranca en minúscula y trae varias palabras
    # ("correspondiente factura. En cumplimiento...", "dirigir un escrito a...").
    if cleaned[:1].islower() and len(cleaned.split()) >= 4:
        return True
    return False


def _repair_party_roles(doc: dict, text: str, confidences: dict[str, Decimal]) -> None:
    """Corrige confusiones emisor/receptor usando evidencia del propio PDF.

    El Invoice Parser confunde con frecuencia el bloque "Bill to" con el
    proveedor. La empresa propia y sus tax IDs son configuración de contexto,
    no datos inventados: sólo se usan para decidir roles entre valores que ya
    aparecen en el documento.
    """
    supplier = _clean_party_name(doc.get("proveedor_nombre_comercial") or "") or None
    legal = _clean_party_name(doc.get("proveedor_razon_social_legal") or "") or None
    receiver = _clean_party_name(doc.get("cliente_nombre") or "") or None
    supplier_tax = _clean_tax_id_candidate(doc.get("proveedor_tax_id"))
    receiver_tax = _clean_tax_id_candidate(doc.get("cliente_tax_id"))

    original_supplier, original_receiver = supplier, receiver
    if _is_own_party(supplier) and receiver and not _is_own_party(receiver):
        supplier, receiver = receiver, supplier
        legal = supplier
    elif _is_own_party(legal) and receiver and not _is_own_party(receiver):
        supplier, receiver = receiver, legal
        legal = supplier

    candidates = [name for _, name in _legal_name_candidates(text)]
    own_names = [name for name in candidates if _is_own_party(name)]
    non_own_names = [name for name in candidates if not _is_own_party(name)]

    all_tax_ids = [_clean_tax_id_candidate(value) for value in _tax_ids(text)]
    all_tax_ids = [value for value in all_tax_ids if value]
    own_tax_ids = [value for value in all_tax_ids if _is_own_tax_id(value)]
    non_own_tax_ids = [value for value in all_tax_ids if not _is_own_tax_id(value)]

    if own_tax_ids:
        receiver_tax = own_tax_ids[0]
        if non_own_tax_ids:
            supplier_tax = non_own_tax_ids[0]
        elif _is_own_tax_id(supplier_tax):
            supplier_tax = None
    elif _is_own_tax_id(supplier_tax) and receiver_tax and not _is_own_tax_id(receiver_tax):
        supplier_tax, receiver_tax = receiver_tax, supplier_tax

    tax_supplier = _name_near_tax_id(text, supplier_tax)
    # El nombre impreso junto al CIF del proveedor es evidencia directa. Cuando
    # existe, NO se recurre a "intercambiar emisor y receptor": en la factura
    # 144 de GESMAR el emisor venía como la empresa propia y el receptor traía
    # el nombre del estudio ("IA EN EL SECTOR AUTOMOVILÍSTICO"), así que el
    # intercambio dejaba un concepto donde debía ir el proveedor. Ese valor
    # parece un nombre válido, de modo que ninguna pasada posterior lo corregía.
    corroborated = bool(tax_supplier) and not _is_own_party(tax_supplier)
    if receiver and not _is_own_party(receiver) and _is_own_party(original_supplier) \
            and not corroborated:
        supplier = receiver
    if (_is_bad_party_name(supplier) or _is_own_party(supplier)) and original_receiver \
            and not corroborated \
            and not _is_bad_party_name(original_receiver) and not _is_own_party(original_receiver):
        supplier = original_receiver
    if corroborated:
        legal = tax_supplier
        if _is_bad_party_name(supplier) or _is_own_party(supplier) \
                or (LEGAL_SUFFIX_RE.search(tax_supplier) and not LEGAL_SUFFIX_RE.search(supplier or "")
                    and _fold(supplier or "") not in _fold(tax_supplier)):
            supplier = tax_supplier
    if (_is_bad_party_name(supplier) or _is_own_party(supplier)) and non_own_names:
        supplier = non_own_names[0]
    if (not legal or _is_bad_party_name(legal) or _is_own_party(legal)) and non_own_names:
        legal = tax_supplier if tax_supplier and not _is_own_party(tax_supplier) else non_own_names[0]

    if own_names:
        if not receiver or not _is_own_party(receiver):
            receiver = own_names[0]
    elif _is_own_party(original_supplier):
        receiver = original_supplier

    # Si el nombre comercial sigue siendo inválido pero la razón social legal es
    # buena, usar la razón social. Caso AMI: el comercial traía un fragmento de
    # frase del texto legal mientras la razón social decía "AMI S.L." correcta.
    if _is_bad_party_name(supplier) and legal and not _is_bad_party_name(legal) \
            and not _is_own_party(legal):
        supplier = legal

    # Cliente sin nombre legible pero con el CIF propio presente en el documento
    # (Telefónica escribe "Estimado cliente"): se repone desde el CIF, que SÍ es
    # evidencia del documento. No se inventa nada.
    if _is_bad_party_name(receiver) and receiver_tax and _is_own_tax_id(receiver_tax):
        known = DEFAULT_OWN_PARTY_BY_TAX_ID.get(_tax_id_key(receiver_tax))
        if known:
            receiver = known

    # Un identificador fiscal con formato implausible es un dato inventado
    # (Zoom: se cargó el nº de transacción "P399851862" como CIF del proveedor).
    # Vale más dejarlo vacío que afirmar un identificador falso.
    if supplier_tax and not _is_plausible_tax_id(supplier_tax):
        supplier_tax = None
    if receiver_tax and not _is_plausible_tax_id(receiver_tax):
        receiver_tax = None

    # Red de seguridad: el proveedor NUNCA puede ser la empresa propia ni un
    # nombre inválido. Si tras todas las reparaciones sigue siéndolo, es preferible
    # dejarlo vacío (y que el control lo marque) antes que afirmar un dato falso
    # —p. ej. cargar al cliente como proveedor, que puede desviar un pago—.
    if _is_own_party(supplier) or _is_bad_party_name(supplier):
        supplier = None
    if legal and (_is_own_party(legal) or _is_bad_party_name(legal)):
        legal = None

    doc["proveedor_nombre_comercial"] = supplier
    doc["proveedor_razon_social_legal"] = legal or supplier
    doc["cliente_nombre"] = receiver
    doc["proveedor_tax_id"] = supplier_tax
    doc["cliente_tax_id"] = receiver_tax
    for field in (
        "proveedor_nombre_comercial", "proveedor_razon_social_legal", "cliente_nombre",
        "proveedor_tax_id", "cliente_tax_id",
    ):
        if doc.get(field) not in (None, ""):
            confidences.setdefault(field, Decimal("0.85"))


def _name_near_country(text: str, country: str) -> str | None:
    aliases = COUNTRY_ALIASES.get(country, {country.casefold()})
    lines = text.splitlines()
    candidates = _legal_name_candidates(text)
    for country_index, line in enumerate(lines):
        if _fold(line).strip(" .,;:-") not in aliases:
            continue
        nearby = [
            (index, name) for index, name in candidates
            if 0 <= country_index - index <= 6
        ]
        if nearby:
            return max(nearby, key=lambda item: item[0])[1]
    return None


def _name_near_tax_id(text: str, tax_id: str | None) -> str | None:
    if not tax_id:
        return None
    compact_tax = re.sub(r"\W", "", tax_id).upper()
    lines = text.splitlines()
    candidates = _legal_name_candidates(text)
    for tax_index, line in enumerate(lines):
        if compact_tax not in re.sub(r"\W", "", line).upper():
            continue
        nearby = [
            (index, name) for index, name in candidates
            if 0 <= tax_index - index <= 6 or index == tax_index
        ]
        if nearby:
            return max(nearby, key=lambda item: item[0])[1]
    return None


def _tax_ids(text: str) -> list[str]:
    values: list[str] = []
    for match in TAX_ID_RE.finditer(text):
        value = re.sub(r"[\s-]", "", match.group(0)).upper()
        if value not in values:
            values.append(value)
    return values


def _labelled_tax_id(text: str, labels: tuple[str, ...]) -> str | None:
    for line in text.splitlines():
        folded = _fold(line)
        if not any(label in folded for label in labels):
            continue
        match = TAX_ID_RE.search(line)
        if match:
            return re.sub(r"[\s-]", "", match.group(0)).upper()
    return None


def _expanded_supplier_name(text: str, supplier: str | None, receiver: str | None) -> str | None:
    if not supplier:
        return None
    supplier_tokens = {
        token for token in re.findall(r"[a-z0-9]+", _fold(supplier))
        if len(token) >= 4 and token not in {"para", "empresa", "empresas"}
    }
    receiver_folded = _fold(receiver or "")
    candidates: list[tuple[int, int, str]] = []
    for _, raw_name in _legal_name_candidates(text):
        cleaned = _clean_party_name(raw_name)
        folded = _fold(cleaned)
        if not cleaned or folded == receiver_folded:
            continue
        overlap = len(supplier_tokens & set(re.findall(r"[a-z0-9]+", folded)))
        if overlap:
            candidates.append((overlap, -len(cleaned), cleaned))
    if candidates:
        return max(candidates)[2]
    return _clean_party_name(supplier)


def _resolve_parties(
    doc: dict,
    entities: dict[str, Any],
    text: str,
    confidences: dict[str, Decimal],
    iban: str | None,
) -> None:
    supplier_entity = entities.get("supplier_name")
    receiver_entity = entities.get("receiver_name")
    supplier_tax_entity = entities.get("supplier_tax_id")
    receiver_tax_entity = entities.get("receiver_tax_id")
    supplier = _entity_value(supplier_entity) if supplier_entity else doc.get("proveedor_nombre_comercial")
    receiver = _entity_value(receiver_entity) if receiver_entity else doc.get("cliente_nombre")
    supplier_tax = _entity_value(supplier_tax_entity) if supplier_tax_entity else doc.get("proveedor_tax_id")
    receiver_tax = _entity_value(receiver_tax_entity) if receiver_tax_entity else doc.get("cliente_tax_id")

    labelled_supplier = _labelled_party(text, "SUPPLIER") or _labelled_party(text, "PROVEEDOR")
    labelled_receiver = _labelled_party(text, "CLIENT") or _labelled_party(text, "CLIENTE")
    all_tax_ids = _tax_ids(text)
    labelled_supplier_tax = _labelled_tax_id(
        text,
        ("nuestro cif", "our vat", "supplier tax", "supplier vat"),
    )
    labelled_receiver_tax = _labelled_tax_id(
        text,
        ("vuestro cif", "your vat", "receiver tax", "customer tax", "client tax"),
    )
    supplier_tax = labelled_supplier_tax or supplier_tax
    receiver_tax = labelled_receiver_tax or receiver_tax
    bank_country = re.sub(r"\W", "", iban or "").upper()[:2] if iban else None
    if bank_country:
        matching = [value for value in all_tax_ids if value.startswith(bank_country)]
        if matching:
            supplier_tax = matching[-1]
            remaining = [value for value in all_tax_ids if value != supplier_tax]
            if remaining:
                receiver_tax = remaining[0]
    normalized_supplier_tax = re.sub(r"[\s-]", "", supplier_tax or "").upper()
    normalized_receiver_tax = re.sub(r"[\s-]", "", receiver_tax or "").upper()
    if not normalized_receiver_tax or normalized_receiver_tax == normalized_supplier_tax:
        remaining = [value for value in all_tax_ids if value != normalized_supplier_tax]
        if remaining:
            receiver_tax = remaining[0]

    country_supplier = _name_near_country(text, bank_country) if bank_country else None
    tax_supplier = _name_near_tax_id(text, supplier_tax)
    # Las heurísticas de país y de CIF sólo valen si NO devuelven la empresa
    # propia: con un IBAN español, "el nombre cerca de España" caía en el bloque
    # del cliente y sustituía al proveedor real (Iván Zimmermann, ELZABURU).
    if country_supplier and _is_own_party(country_supplier):
        country_supplier = None
    if tax_supplier and _is_own_party(tax_supplier):
        tax_supplier = None
    # La entidad del parser es evidencia directa: manda sobre las heurísticas
    # salvo que sea inválida o la propia empresa. Antes iba última y por eso se
    # perdían los nombres de personas físicas, que no llevan forma jurídica y
    # ninguna heurística sabe reconstruir.
    entity_supplier = _clean_party_name(supplier or "") or None
    entity_usable = bool(entity_supplier) and not _is_bad_party_name(entity_supplier) \
        and not _is_own_party(entity_supplier)
    # El nombre impreso junto al CIF del proveedor, cuando trae forma jurídica,
    # es la razón social registrada: gana sobre la entidad del parser, que a
    # menudo captura el logotipo ("AVANOɅ PUNTO PORPUNTO", "II/ELZABURU").
    registered_supplier = tax_supplier if tax_supplier and LEGAL_SUFFIX_RE.search(tax_supplier) else None
    if labelled_supplier:
        supplier = labelled_supplier
        confidences["proveedor_nombre_comercial"] = Decimal("0.95")
    elif registered_supplier:
        supplier = registered_supplier
        confidences["proveedor_nombre_comercial"] = Decimal("0.92")
    elif entity_usable:
        # Sin forma jurídica en la entidad se intenta completar desde el texto;
        # si no hay nada mejor se conserva la entidad. Es el caso de las personas
        # físicas, que no llevan forma jurídica y ninguna heurística reconstruye.
        supplier = _expanded_supplier_name(text, entity_supplier, receiver) or entity_supplier
        confidences["proveedor_nombre_comercial"] = Decimal("0.90")
    elif country_supplier:
        supplier = country_supplier
        confidences["proveedor_nombre_comercial"] = Decimal("0.90")
    elif tax_supplier:
        supplier = tax_supplier
        confidences["proveedor_nombre_comercial"] = Decimal("0.90")
    else:
        supplier = _expanded_supplier_name(text, supplier, receiver)

    if labelled_receiver:
        receiver = labelled_receiver
        confidences["cliente_nombre"] = Decimal("0.95")
    if _is_bad_party_name(receiver):
        receiver = None
    if receiver is None:
        supplier_folded = _fold(supplier or "")
        alternatives = [
            name for _, name in _legal_name_candidates(text)
            if _fold(name) != supplier_folded and not _is_bad_party_name(name)
        ]
        if alternatives:
            receiver = alternatives[0]
            confidences["cliente_nombre"] = Decimal("0.85")

    if supplier:
        supplier = _clean_party_name(supplier)
        doc["proveedor_nombre_comercial"] = supplier
        # Nombre comercial y razón social son campos distintos del esquema y se
        # estaban rellenando con el mismo valor. "Sabores Patagónicos" factura
        # como GAYARETAS 2018 S.L: para conciliar con el maestro de proveedores
        # y para pagar, la denominación con forma jurídica es la que importa.
        legal_name = supplier if LEGAL_SUFFIX_RE.search(supplier) else (
            registered_supplier
            or next(
                (name for _, name in _legal_name_candidates(text)
                 if not _is_own_party(name) and not _is_bad_party_name(name)),
                None,
            )
        )
        doc["proveedor_razon_social_legal"] = legal_name or supplier
    if receiver:
        doc["cliente_nombre"] = _clean_party_name(receiver)
    else:
        doc["cliente_nombre"] = None
    if supplier_tax:
        doc["proveedor_tax_id"] = re.sub(r"[\s-]", "", supplier_tax).upper()
    if receiver_tax and re.sub(r"[\s-]", "", receiver_tax).upper() != doc.get("proveedor_tax_id"):
        doc["cliente_tax_id"] = re.sub(r"[\s-]", "", receiver_tax).upper()
    elif doc.get("cliente_tax_id") == doc.get("proveedor_tax_id"):
        doc["cliente_tax_id"] = None

    if supplier_entity and "proveedor_nombre_comercial" not in confidences:
        confidences["proveedor_nombre_comercial"] = _entity_confidence(supplier_entity)
    if receiver_entity and doc.get("cliente_nombre") and "cliente_nombre" not in confidences:
        confidences["cliente_nombre"] = _entity_confidence(receiver_entity)
    _repair_party_roles(doc, text, confidences)


def _reconcile_tax(doc: dict, text: str, confidences: dict[str, Decimal]) -> None:
    percentages = [
        Decimal(raw.replace(",", "."))
        for raw in re.findall(r"\b(\d{1,2}(?:[,.]\d+)?)\s*%", text)
    ]
    def decimal_field(field: str) -> Decimal | None:
        value = doc.get(field)
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return None

    net = decimal_field("importe_neto")
    tax = decimal_field("importe_iva")
    total = decimal_field("importe_total")

    if net and tax is not None and percentages:
        implied = (tax / net) * Decimal("100")
        best = min(percentages, key=lambda value: abs(value - implied))
        if abs(best - implied) <= Decimal("0.25"):
            doc["tipo_iva"] = format(best.normalize(), "f")
            confidences["tipo_iva"] = Decimal("0.90")

    folded = _fold(text)
    reverse_charge = any(term in folded for term in (
        "reverse charge", "reverse-charged", "inversion del sujeto pasivo", "article 196",
    ))
    if reverse_charge and Decimal("0") in percentages:
        doc["tipo_iva"] = "0"
        confidences["tipo_iva"] = Decimal("0.90")
        if net is not None and total is not None and abs(net - total) <= Decimal("0.02"):
            doc["importe_iva"] = "0.00"
            confidences["importe_iva"] = Decimal("0.85")


def _set_entity(
    doc: dict,
    confidences: dict[str, Decimal],
    entities: dict[str, Any],
    field: str,
    entity_types: tuple[str, ...],
    transform: Callable[[str | None], str | None] = lambda value: value,
) -> None:
    for entity_type in entity_types:
        entity = entities.get(entity_type)
        if entity is None:
            continue
        value = transform(_entity_value(entity))
        if value not in (None, ""):
            doc[field] = value
            confidences[field] = _entity_confidence(entity)
            return


def _tax_rate_from_text(text: str) -> str | None:
    match = re.search(
        r"(?i)\b(?:VAT|IVA|TVA)(?:\s+RATE)?\s*[:(]?\s*(\d{1,2}(?:[,.]\d+)?)\s*%",
        text,
    )
    return _decimal_value(match.group(1), rate=True) if match else None


def _payment_method(text: str, has_bank_details: bool = False) -> str:
    folded = _fold(text)
    if re.search(r"\b(direct debit|domiciliacion|domiciliado|sepa|cargo en cuenta)\b", folded):
        return "domiciliacion_direct_debit"
    # La transferencia gana sobre la tarjeta: muchas facturas piden transferencia
    # y sólo listan las tarjetas que el emisor acepta como alternativa. Qualzy
    # dice "Please transfer the total amount payable" y salía como tarjeta por
    # el pie "Accepts Visa, Mastercard".
    if re.search(
        r"\b(transferencia|bank transfer|wire transfer|direct transfer|"
        r"transfer the total|virement)\b",
        folded,
    ):
        return "transferencia"
    if re.search(r"\b(tarjeta|card payment|credit card|visa|mastercard)\b", folded):
        return "tarjeta"
    return "no_indicado"


#: Estados miembro de la UE (sin GB: post-Brexit es extracomunitario).
_EU_VAT_COUNTRIES = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT",
    "RO", "SE", "SI", "SK",
}


def _treatment_by_country(doc: dict) -> str | None:
    """Deduce el tratamiento por el país del proveedor (VAT o IBAN).

    Un proveedor extranjero no puede ser una operación "nacional": el caso belga
    y el británico del lote de enero quedaban mal clasificados por no mirar el país.
    """
    for value, kind in (
        (str(doc.get("proveedor_tax_id") or ""), "vat"),
        (str(doc.get("iban") or ""), "iban"),
    ):
        compact = re.sub(r"\W", "", value).upper()
        if len(compact) < 4 or not compact[:2].isalpha():
            continue
        country = compact[:2]
        if country == "ES":
            return None
        if country in _EU_VAT_COUNTRIES:
            return "intracomunitario_inversion_sujeto_pasivo"
        return "extracomunitario"
    return None


#: La exención debe estar referida al IVA/VAT/TVA, y no ser una mención suelta
#: dentro del texto legal de la factura.
_EXEMPTION_RE = re.compile(
    r"(?:iva|vat|tva|impuesto|operacion|operaciones|entrega|entregas)\s+"
    r"(?:\w+\s+){0,2}?(?:exent[ao]s?|exempt|exoner\w+)"
    r"|(?:exent[ao]s?|exempt|exoner\w+)\s+(?:\w+\s+){0,2}?(?:iva|vat|tva)"
)


def _tax_treatment(doc: dict, text: str) -> str | None:
    folded = _fold(text)
    if any(term in folded for term in (
        "reverse charge", "reverse-charged", "inversion del sujeto pasivo",
        "article 196", "articulo 196",
    )):
        return "intracomunitario_inversion_sujeto_pasivo"
    # El país manda sobre la mención de exención: una factura francesa al 0% es
    # intracomunitaria, no "exenta". Antes esta rama iba primero y cualquier
    # aparición suelta de "exento" ganaba (la factura de Endesa, con IVA 21%
    # real, salía exenta por el texto legal del bono social).
    by_country = _treatment_by_country(doc)
    if by_country:
        return by_country
    # La exención tiene que estar referida al IVA, no ser una palabra suelta.
    if _EXEMPTION_RE.search(folded):
        return "exento_otro"
    try:
        rate = Decimal(str(doc.get("tipo_iva"))) if doc.get("tipo_iva") is not None else None
        tax = Decimal(str(doc.get("importe_iva"))) if doc.get("importe_iva") is not None else None
    except InvalidOperation:
        rate = tax = None
    if rate == 0 and tax in (None, Decimal("0")):
        return "exento_otro"
    if rate is not None or tax is not None:
        return "nacional"
    return None


def _money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


_AMOUNT_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9])[-+]?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?"
    r"|(?<![A-Z0-9])[-+]?\d+(?:[.,]\d{2})(?!\d)",
    re.IGNORECASE,
)


def _amount_token_value(token: str) -> Decimal | None:
    raw = token.strip()
    if "," in raw and "." not in raw and len(raw.rsplit(",", 1)[1]) == 3:
        raw = raw.replace(",", "")
    elif "." in raw and "," not in raw and len(raw.rsplit(".", 1)[1]) == 3:
        raw = raw.replace(".", "")
    value = _decimal_value(raw)
    return _money(value)


_RETENTION_LINE_RE = re.compile(r"(?i)\b(?:irpf|retenci[oó]n|retencion|withholding)\b")


def _repair_net_from_identity(
    doc: dict, text: str, confidences: dict[str, Decimal]
) -> None:
    """Repara importe_neto usando neto + IVA - retencion = total.

    El parser sólo emite net_amount en una minoría de facturas; cuando falta, el
    neto se rellenaba con el primer número plausible del documento y salía
    cualquier cosa (el porcentaje de IRPF en Rebeca, el propio IVA en Iván, un
    código de artículo en Agua Life). El total y el IVA sí vienen etiquetados y
    con confianza alta, así que el neto se despeja en vez de adivinarse.

    Para no reparar a ciegas se exige que el tipo implícito por el despeje
    coincida con un porcentaje declarado en el documento: si no coincide, el
    dato dudoso no es el neto y se deja como está.
    """
    total = _money(doc.get("importe_total"))
    tax = _money(doc.get("importe_iva"))
    net = _money(doc.get("importe_neto"))
    retencion = _money(doc.get("retencion_irpf")) or Decimal("0")
    if total is None or tax is None or total <= 0:
        return
    if net is not None and abs(net + tax - retencion - total) <= Decimal("0.02"):
        return  # la terna ya cuadra: no hay nada que reparar

    declared = [
        Decimal(raw.replace(",", "."))
        for raw in re.findall(r"\b(\d{1,2}(?:[,.]\d+)?)\s*%", text)
    ]
    present = {
        value
        for value in (
            _amount_token_value(match.group(0)) for match in _AMOUNT_TOKEN_RE.finditer(text)
        )
        if value is not None
    }
    candidate = (total - tax + retencion).quantize(Decimal("0.01"))
    if candidate > 0:
        if tax == 0:
            implied_ok = True
        else:
            implied = (tax / candidate) * Decimal("100")
            implied_ok = any(abs(rate - implied) <= Decimal("0.3") for rate in declared)
            # Algunas facturas imprimen el tipo en una columna separada del
            # signo "%" (Agua Life, Viñals), así que no hay porcentaje que
            # comparar. El despeje se acepta igual si el importe resultante
            # figura literalmente en el documento: es evidencia, no inferencia.
            implied_ok = implied_ok or candidate in present
        if implied_ok:
            doc["importe_neto"] = f"{candidate}"
            confidences["importe_neto"] = Decimal("0.88")
            return

    # El despeje directo no cierra: o falta la retención, o el propio IVA es
    # malo. Antes de nada se prueba la partición por tipo declarado exigiendo
    # que AMBOS números aparezcan literalmente en el documento: en la factura de
    # Endesa el parser erró neto e IVA a la vez (259,39 / 68,19) y la partición
    # correcta al 21% (263,99 / 55,44) sí está impresa en el cuadro de totales.
    if not retencion:
        for rate in sorted({value for value in declared if value > 0}, reverse=True):
            base = (total / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("0.01"))
            split_tax = (total - base).quantize(Decimal("0.01"))
            if base in present and split_tax in present:
                doc["importe_neto"] = f"{base}"
                doc["importe_iva"] = f"{split_tax}"
                doc["tipo_iva"] = format(rate.normalize(), "f")
                confidences["importe_neto"] = Decimal("0.86")
                confidences["importe_iva"] = Decimal("0.86")
                confidences["tipo_iva"] = Decimal("0.86")
                return

    # Sigue sin cerrar: suele faltar la retención, porque en layouts en columna
    # el importe cae varias líneas debajo de la etiqueta "IRPF" y el extractor
    # por línea no la ve (caso Rebeca Ferrer). Con el tipo de IVA declarado el
    # neto sale del propio IVA, y la retención queda como residuo verificable
    # contra otro porcentaje declarado.
    if tax <= 0 or retencion:
        return
    for rate in {value for value in declared if value > 0}:
        base = (tax / (rate / Decimal("100"))).quantize(Decimal("0.01"))
        residual = (base + tax - total).quantize(Decimal("0.01"))
        if residual <= 0 or residual >= base:
            continue
        residual_rate = (residual / base) * Decimal("100")
        if not any(abs(other - residual_rate) <= Decimal("0.3") for other in declared):
            continue
        doc["importe_neto"] = f"{base}"
        doc["retencion_irpf"] = f"{residual}"
        confidences["importe_neto"] = Decimal("0.85")
        confidences["retencion_irpf"] = Decimal("0.85")
        return


def _retention_amount(text: str) -> Decimal | None:
    """Importe de la retención practicada (IRPF / retención a cuenta).

    No usa el token de importe genérico porque las facturas de autónomos suelen
    escribir el importe sin decimales ("- 15% IRPF 375€"), y ese token exige
    decimales o separador de miles.
    """
    for line in text.splitlines():
        if not _RETENTION_LINE_RE.search(line):
            continue
        # Quitar los porcentajes ("15%") para no confundir el tipo con el importe.
        cleaned = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", line)
        values = []
        for match in re.finditer(r"[-+]?\d[\d.,]*", cleaned):
            parsed = _money(_decimal_value(match.group(0)))
            if parsed is not None and parsed != 0:
                values.append(abs(parsed))
        if values:
            return max(values)
    return None


def _labelled_amount(
    text: str,
    labels: tuple[str, ...],
    *,
    reject: tuple[str, ...] = (),
    pick: str = "last",
) -> Decimal | None:
    lines = text.splitlines()
    for label in labels:
        for line in lines:
            folded = _fold(line)
            if label not in folded or any(item in folded for item in reject):
                continue
            # Separa la moneda pegada al número ("EUR105.98"): el token de
            # importe exige que no venga precedido de letra/dígito, así que sin
            # esto la línea "Invoice Total EUR105.98" no rendía ningún importe.
            scan = re.sub(r"(?i)\b(EUR|USD|GBP|CHF)(?=\d)", r"\1 ", line)
            values = [
                value for value in (_amount_token_value(match.group(0)) for match in _AMOUNT_TOKEN_RE.finditer(scan))
                if value is not None
            ]
            if values:
                return values[0] if pick == "first" else values[-1]
    return None


def refine_mapped_document(
    doc: dict,
    text: str,
    local_document: dict | None = None,
    confidences: dict[str, Decimal] | None = None,
) -> dict:
    """Refinamiento determinístico reutilizable, sin una nueva llamada cloud."""
    confidences = confidences if confidences is not None else {}
    local_document = local_document or {}
    _repair_party_roles(doc, text, confidences)

    historical = extract_historical_fields(text)
    doc["proveedor_registro"] = (
        historical.proveedor_registro.value if historical.proveedor_registro else None
    )
    if historical.proveedor_registro:
        confidences["proveedor_registro"] = Decimal(str(historical.proveedor_registro.confidence))
    doc["condiciones_pago"] = (
        historical.condiciones_pago.value if historical.condiciones_pago else None
    )
    if historical.condiciones_pago:
        confidences["condiciones_pago"] = Decimal(str(historical.condiciones_pago.confidence))
    if historical.periodo_servicio_desde and historical.periodo_servicio_hasta:
        doc["periodo_servicio_desde"] = historical.periodo_servicio_desde.value
        doc["periodo_servicio_hasta"] = historical.periodo_servicio_hasta.value
        confidences["periodo_servicio_desde"] = Decimal(str(historical.periodo_servicio_desde.confidence))
        confidences["periodo_servicio_hasta"] = Decimal(str(historical.periodo_servicio_hasta.confidence))

    # PO sólo con evidencia etiquetada explícitamente. El extractor local ya
    # implementa esa política; el entity parser suele promover referencias
    # genéricas o números de contrato a purchase_order.
    doc["po_reference"] = local_document.get("po_reference")
    if local_document.get("project_reference") and not doc.get("project_reference"):
        doc["project_reference"] = local_document["project_reference"]

    current_total = _money(doc.get("importe_total"))
    # "importe adeudado" / "amount due" NO son el total de la factura: son el
    # saldo que queda por pagar. En ECOTISA ("Pagado 44,38 / Importe adeudado
    # 0,00") ese 0,00 pisaba el total correcto y arrastraba neto e IVA a cero.
    # Se leen más abajo como saldo_pendiente.
    strong_total = _labelled_amount(
        text,
        ("total a pagar", "invoice total", "total factura", "gross",
         "incl btw", "incl. btw", "inclusief btw", "totaal incl", "totaal"),
        reject=("subtotal", "total net", "total excl", "excl btw", "excl. btw", "descuentos", "total a percibir"),
    )
    generic_total = _labelled_amount(
        text,
        ("total",),
        reject=("subtotal", "total net", "total excl", "descuentos", "total a percibir"),
    )
    labelled_net = _labelled_amount(
        text,
        ("base imponible", "net amount", "importe neto", "total net", "total excl", "subtotal",
         "excl btw", "excl. btw", "exclusief btw", "excl vat", "netto", "bedrag excl"),
        reject=("incl btw", "inclusief btw"),
        pick="first",
    )
    labelled_tax = _labelled_amount(
        text,
        ("importe iva", "vat amount", "impuestos", "iva", "vat", "tva"),
        reject=("vat number", "vat no", "cif", "nif"),
    )
    labelled_total = strong_total
    if labelled_total is None and generic_total is not None:
        if current_total is None or current_total == 0:
            labelled_total = generic_total
        else:
            scale = abs(generic_total / current_total)
            if Decimal("990") <= scale <= Decimal("1010") \
                    or Decimal("0.00099") <= scale <= Decimal("0.00101"):
                labelled_total = generic_total
    # Un total etiquetado en cero nunca pisa un total ya extraído: el cero de un
    # documento suele ser un saldo saldado, no el importe de la factura.
    if labelled_total is not None and not (labelled_total == 0 and current_total):
        doc["importe_total"] = f"{labelled_total.quantize(Decimal('0.01'))}"
    if labelled_net is not None and doc.get("importe_neto") in (None, ""):
        doc["importe_neto"] = f"{labelled_net.quantize(Decimal('0.01'))}"
    if labelled_tax is not None and doc.get("importe_iva") in (None, ""):
        doc["importe_iva"] = f"{labelled_tax.quantize(Decimal('0.01'))}"

    local_total = _money(local_document.get("importe_total"))
    total = _money(doc.get("importe_total"))
    if local_total is not None and total is not None and total != 0:
        ratio = abs(local_total / total)
        if ratio in (Decimal("1000"), Decimal("0.001")):
            doc["importe_total"] = f"{local_total.quantize(Decimal('0.01'))}"
            total = local_total

    # Si la terna local cuadra exactamente con el total administrado, es una
    # evidencia más fuerte que una entidad aislada tomada de una línea.
    local_net = _money(local_document.get("importe_neto"))
    local_tax = _money(local_document.get("importe_iva"))
    if total is not None and local_net is not None and local_tax is not None \
            and abs(local_net + local_tax - total) <= Decimal("0.02"):
        doc["importe_neto"] = f"{local_net.quantize(Decimal('0.01'))}"
        doc["importe_iva"] = f"{local_tax.quantize(Decimal('0.01'))}"

    folded = _fold(text)
    reverse_charge = any(term in folded for term in (
        "reverse charge", "reverse-charged", "inversion del sujeto pasivo", "article 196",
    ))
    labelled_rate = _tax_rate_from_text(text)
    if reverse_charge:
        doc["tipo_iva"] = "0"
        doc["importe_iva"] = "0.00"
        if total is not None:
            doc["importe_neto"] = f"{total.quantize(Decimal('0.01'))}"
    elif labelled_rate is not None:
        doc["tipo_iva"] = labelled_rate
        rate = _money(labelled_rate)
        total = _money(doc.get("importe_total"))
        net = _money(doc.get("importe_neto"))
        tax = _money(doc.get("importe_iva"))
        has_withholding = any(term in folded for term in ("irpf", "retencion", "withholding"))
        if total is not None and rate is not None and not has_withholding:
            expected_net = (total / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("0.01"))
            expected_tax = (total - expected_net).quantize(Decimal("0.01"))
            inconsistent = net is None or tax is None or abs(net + tax - total) > Decimal("0.02")
            implausible = (net is not None and net > total * 10) or (tax is not None and tax > total)
            if inconsistent or implausible:
                doc["importe_neto"] = f"{expected_net:.2f}"
                doc["importe_iva"] = f"{expected_tax:.2f}"

    # Total 0/ausente pero con base e IVA extraídos: recomponer para no marcar
    # falsamente "importe no positivo" ni retener la factura por un cero espurio.
    final_total = _money(doc.get("importe_total"))
    final_net = _money(doc.get("importe_neto"))
    final_tax = _money(doc.get("importe_iva"))
    if (final_total is None or final_total == 0) and final_net is not None and final_net > 0:
        recomposed = final_net + (final_tax or Decimal("0"))
        doc["importe_total"] = f"{recomposed.quantize(Decimal('0.01'))}"

    # Retención practicada (IRPF / retención a cuenta): el total a pagar es
    # neto + IVA - retención. Sin este campo, una factura de autónomo descuadra
    # y no se sabe cuánto pagar realmente.
    retencion = _retention_amount(text)
    if retencion is not None and retencion != 0:
        doc["retencion_irpf"] = f"{abs(retencion).quantize(Decimal('0.01'))}"

    _repair_net_from_identity(doc, text, confidences)

    # Saldo pendiente: 0 significa factura YA PAGADA (Zoom cobró con tarjeta).
    # Es la señal que evita un doble pago.
    # "importe adeudado" y "importe a pagar" entran acá y no como total: en
    # ELZABURU el total es 6.467,44 pero, descontada una provisión de fondos ya
    # recibida, sólo quedan 1.022,14 por pagar. Pagar el total sobrepagaría.
    # "total a percibir" cierra el mismo agujero por otra etiqueta: la factura
    # 144 de GESMAR suma 4.591,95 pero descuenta un anticipo de 2.040,00 ya
    # facturado aparte, y el importe real a pagar son 2.551,95. Las dos facturas
    # venían en el mismo lote: pagar ambas completas sobrepagaba el anticipo.
    saldo = _labelled_amount(
        text,
        ("invoice balance", "saldo pendiente", "balance due", "importe pendiente",
         "saldo a pagar", "outstanding balance", "importe adeudado", "amount due",
         "importe a pagar", "amount payable", "total a percibir", "liquido a percibir"),
    )
    if saldo is not None:
        doc["saldo_pendiente"] = f"{saldo.quantize(Decimal('0.01'))}"

    # Sin tipo de IVA declarado y con una terna que no cuadra, la partición
    # neto/IVA no es fiable: el documento no desglosa impuestos (caso Field'N Feel,
    # con inversión del sujeto pasivo, donde se inventó 2028 + 1814 para un total
    # de 3836). Se toma neto = total e IVA = 0 en vez de fabricar un desglose.
    _t = _money(doc.get("importe_total"))
    _n = _money(doc.get("importe_neto"))
    _i = _money(doc.get("importe_iva"))
    # Con retención/IRPF el descuadre es ESPERADO (total = neto + IVA - retención)
    # y NO significa que el desglose sea inventado: sin esta guarda se pisaba el
    # neto correcto de una factura de autónomo con el total.
    _has_retention = any(
        term in folded for term in ("irpf", "retencion", "withholding", "a percibir")
    )
    if doc.get("tipo_iva") in (None, "") and not _has_retention \
            and None not in (_t, _n, _i) and abs(_n + _i - _t) > Decimal("0.02"):
        doc["importe_neto"] = f"{_t.quantize(Decimal('0.01'))}"
        doc["importe_iva"] = "0.00"
        doc["tipo_iva"] = "0"

    # Vencimiento anterior a la emisión: imposible. Suele venir de haber tomado
    # la fecha de operación o del período de servicio como si fuera vencimiento
    # (caso Cabify: emisión 01/01/2026 con "vencimiento" 17/12/2025).
    issue = str(doc.get("fecha_emision") or "")
    due = str(doc.get("fecha_vencimiento_calculada") or "")
    if issue and due and due < issue:
        doc["fecha_vencimiento_calculada"] = None
        doc["fecha_vencimiento_texto"] = None
        confidences.pop("fecha_vencimiento_calculada", None)
        confidences.pop("fecha_vencimiento_texto", None)

    _reconcile_tax(doc, text, confidences)
    doc["metodo_pago"] = _payment_method(text)
    doc["tratamiento_iva"] = _tax_treatment(doc, text)
    confidences.setdefault("metodo_pago", Decimal("0.85"))
    if doc.get("tratamiento_iva"):
        confidences.setdefault("tratamiento_iva", Decimal("0.90"))
    return doc


def _validate_invoice(doc: dict, text: str, confidences: dict[str, Decimal]) -> list[str]:
    warnings: list[str] = []
    missing = [field for field in INVOICE_CRITICAL_FIELDS if doc.get(field) in (None, "")]
    if missing:
        warnings.append("campos criticos ausentes: " + ", ".join(missing))

    supplier = _fold(doc.get("proveedor_nombre_comercial") or "")
    receiver = _fold(doc.get("cliente_nombre") or "")
    if supplier and receiver and supplier == receiver:
        warnings.append("proveedor y cliente no pueden ser la misma entidad")
    # Confusión emisor/receptor: si el proveedor extraído es la propia empresa
    # (el comprador), el extractor tomó el bloque "Facturar a" como emisor.
    # run1 mostró este error con confianza 1.00, por eso no depende del score.
    # Usa la MISMA lista de empresa propia que el corrector de roles
    # (_is_own_party / DEFAULT_OWN_COMPANY_NAMES), no un default aparte.
    if doc.get("proveedor_nombre_comercial") and _is_own_party(
        doc.get("proveedor_nombre_comercial")
    ):
        warnings.append(
            "el proveedor extraido coincide con la empresa propia: "
            "posible confusion emisor/receptor")

    try:
        net = Decimal(str(doc["importe_neto"]))
        tax = Decimal(str(doc["importe_iva"]))
        total = Decimal(str(doc["importe_total"]))
        # Con retención el total es neto + IVA - retención. Se RECONCILIA con el
        # importe extraído en vez de silenciar la validación: silenciarla sin
        # comprobar enmascaraba errores reales de importe.
        retencion = _money(doc.get("retencion_irpf")) or Decimal("0")
        if abs((net + tax - retencion) - total) > Decimal("0.02"):
            deduction = (net + tax) - total
            folded_total = _fold(text)
            has_deduction_note = any(
                term in folded_total
                for term in (
                    "irpf", "retencion", "withholding", "descuento", "discount",
                    "rappel", "bonificacion", "a percibir", "liquido",
                )
            )
            if retencion or not (has_deduction_note and deduction > Decimal("0")):
                # Si la retención ya se conocía y aun así no cuadra, es un
                # descuadre real y debe avisarse.
                warnings.append("base + IVA no coincide con el total")
    except (InvalidOperation, TypeError, KeyError):
        pass

    if bank_evidence_present(text) and not any((
        doc.get("iban"), doc.get("bic"), doc.get("proveedor_banco"),
        doc.get("proveedor_cuenta_bancaria"),
    )):
        warnings.append("hay datos bancarios visibles pero no se pudieron estructurar")
    if doc.get("iban") and not doc.get("iban_enmascarado") and not is_valid_iban(doc["iban"]):
        warnings.append("IBAN con formato o checksum invalido")
    if doc.get("bic") and not is_valid_bic(doc["bic"]):
        warnings.append("BIC/SWIFT invalido")
    account = doc.get("proveedor_cuenta_bancaria")
    if account and re.sub(r"\D", "", account).isdigit() and len(re.sub(r"\D", "", account)) == 20 \
            and not is_valid_spanish_ccc(account):
        warnings.append("CCC espanol con digitos de control invalidos")

    low = [
        field for field in INVOICE_CRITICAL_FIELDS
        if field in confidences and confidences[field] < Decimal("0.60")
    ]
    if low:
        warnings.append("baja confianza en: " + ", ".join(low))
    warnings.extend(validate_document(doc))
    return warnings


def _result_confidence(doc: dict, confidences: dict[str, Decimal]) -> Decimal:
    present = [field for field in INVOICE_CRITICAL_FIELDS if doc.get(field) not in (None, "")]
    completeness = Decimal(len(present)) / Decimal(len(INVOICE_CRITICAL_FIELDS))
    qualities = [confidences.get(field, Decimal("0.85")) for field in present]
    quality = sum(qualities, Decimal("0")) / Decimal(len(qualities)) if qualities else Decimal("0")
    return (completeness * quality).quantize(Decimal("0.01"))


def map_document_ai_result(filename: str, cloud_document: Any) -> PocResult:
    text = getattr(cloud_document, "text", "") or ""
    pages = max(1, len(getattr(cloud_document, "pages", ()) or ()))
    baseline_pdf = PdfText(path=Path(filename), pages=pages, text=text)
    baseline = extract_document(baseline_pdf)
    doc = dict(baseline.document)
    doc["document_type"] = "invoice"
    confidences: dict[str, Decimal] = {"document_type": Decimal("1.00")}
    entities = _best_entities(cloud_document)

    _set_entity(doc, confidences, entities, "numero_factura", ("invoice_id",))
    _set_entity(doc, confidences, entities, "fecha_emision", ("invoice_date",), _date_value)
    _set_entity(doc, confidences, entities, "fecha_vencimiento_calculada", ("due_date",), _date_value)
    due_entity = entities.get("due_date")
    due_mention = _entity_mention(due_entity) if due_entity is not None else None
    # Un centinela tipo "31/12/9999" no es una condición de pago: es la forma en
    # que el ERP del emisor dice "sin vencimiento". Guardarlo confundía al revisor.
    if due_mention and _looks_like_sentinel_date(due_mention):
        due_mention = None
    if due_mention:
        doc["fecha_vencimiento_texto"] = due_mention
        confidences["fecha_vencimiento_texto"] = _entity_confidence(due_entity)
    _set_entity(doc, confidences, entities, "proveedor_nombre_comercial", ("supplier_name",))
    _set_entity(doc, confidences, entities, "proveedor_tax_id", ("supplier_tax_id",))
    _set_entity(doc, confidences, entities, "proveedor_registro", ("supplier_registration",))
    _set_entity(doc, confidences, entities, "cliente_nombre", ("receiver_name",))
    _set_entity(doc, confidences, entities, "cliente_tax_id", ("receiver_tax_id",))
    _set_entity(doc, confidences, entities, "moneda", ("currency",), _currency_value)
    _set_entity(doc, confidences, entities, "importe_neto", ("net_amount",), _decimal_value)
    _set_entity(doc, confidences, entities, "importe_iva", ("total_tax_amount", "vat/tax_amount"), _decimal_value)
    _set_entity(doc, confidences, entities, "importe_total", ("total_amount",), _decimal_value)
    _set_entity(doc, confidences, entities, "tipo_iva", ("vat/tax_rate",), lambda value: _decimal_value(value, rate=True))
    _set_entity(doc, confidences, entities, "condiciones_pago", ("payment_terms",))

    if doc.get("tipo_iva") is None:
        rate = _tax_rate_from_text(text)
        if rate is not None:
            doc["tipo_iva"] = rate
            confidences["tipo_iva"] = Decimal("0.90")

    preferred_iban_entity = entities.get("supplier_iban")
    preferred_iban = _entity_value(preferred_iban_entity) if preferred_iban_entity else None
    bank = extract_bank_details(text, preferred_iban=preferred_iban)
    doc["iban"] = bank.iban
    doc["iban_enmascarado"] = bank.iban_enmascarado
    doc["bic"] = bank.bic
    doc["proveedor_banco"] = bank.banco
    doc["proveedor_cuenta_bancaria"] = bank.cuenta
    if preferred_iban_entity and bank.iban:
        confidences["iban"] = _entity_confidence(preferred_iban_entity)
    for field, value in (
        ("bic", bank.bic),
        ("proveedor_banco", bank.banco),
        ("proveedor_cuenta_bancaria", bank.cuenta),
    ):
        if value:
            confidences[field] = Decimal("0.90")

    _resolve_parties(doc, entities, text, confidences, bank.iban)
    refine_mapped_document(doc, text, baseline.document, confidences)

    warnings = _validate_invoice(doc, text, confidences)
    return PocResult(
        doc_id=Path(filename).stem,
        archivo=filename,
        pages=pages,
        text_chars=len(text),
        confidence=_result_confidence(doc, confidences),
        warnings=warnings,
        document=doc,
        engine=ENGINE_NAME,
        field_confidences=confidences,
    )


def _document_ai_client(config: DocumentAIConfig):
    from google.api_core.client_options import ClientOptions  # type: ignore
    from google.cloud import documentai_v1 as documentai  # type: ignore

    credentials = None
    access_token = os.getenv("DOCUMENT_AI_ACCESS_TOKEN")
    if access_token:
        from google.oauth2.credentials import Credentials  # type: ignore
        credentials = Credentials(token=access_token)
    options = ClientOptions(api_endpoint=f"{config.location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(
        credentials=credentials,
        client_options=options,
    )


def _cache_dir() -> Path | None:
    """Directorio de caché de respuestas de Document AI, o None si está apagado.

    Es una herramienta de desarrollo y evaluación: durante un ciclo golden se
    reprocesa el mismo lote decenas de veces y cada vuelta son cientos de
    llamadas a la API. Apagada por defecto; en producción nunca se activa, así
    que ninguna factura real se escribe a disco por esta vía.
    """
    raw = os.getenv("AP_DOCUMENT_AI_CACHE_DIR", "").strip()
    return Path(raw) if raw else None


def _cache_key(data: bytes, config: DocumentAIConfig) -> str:
    """La clave incluye el processor: cambiarlo debe invalidar la caché."""
    digest = hashlib.sha256()
    digest.update(data)
    digest.update(b"\0")
    digest.update(f"{config.project_id}/{config.location}/{config.processor_id}".encode())
    return digest.hexdigest()


def _cached_document(key: str, documentai: Any) -> Any | None:
    directory = _cache_dir()
    if directory is None:
        return None
    path = directory / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return documentai.Document.from_json(
            path.read_text(encoding="utf-8"), ignore_unknown_fields=True
        )
    except Exception:  # noqa: BLE001 - una caché corrupta nunca debe romper la extracción
        return None


def _store_document(key: str, document: Any, documentai: Any) -> None:
    directory = _cache_dir()
    if directory is None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.json").write_text(
            documentai.Document.to_json(document), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - no poder cachear no es un error de negocio
        pass


def process_invoice_bytes(
    filename: str,
    data: bytes,
    config: DocumentAIConfig,
    *,
    require_invoice_evidence: bool = True,
) -> PocResult:
    from google.cloud import documentai_v1 as documentai  # type: ignore

    key = _cache_key(data, config)
    document = _cached_document(key, documentai)
    if document is None:
        client = _document_ai_client(config)
        name = client.processor_path(config.project_id, config.location, config.processor_id)
        request = documentai.ProcessRequest(
            name=name,
            raw_document=documentai.RawDocument(content=data, mime_type="application/pdf"),
            # El processor administrado admite hasta 30 páginas en modo imageless
            # (15 en el modo estándar). Varias facturas reales incluyen anexos de
            # detalle extensos; habilitarlo evita rechazarlas antes de extraer.
            imageless_mode=True,
        )
        document = client.process_document(request=request, timeout=90).document
        _store_document(key, document, documentai)
    response = SimpleNamespace(document=document)
    entity_types = {
        getattr(entity, "type_", None) or getattr(entity, "type", None)
        for entity in getattr(response.document, "entities", ()) or ()
    }
    missing_evidence = [
        entity_type
        for entity_type in ("invoice_id", "total_amount")
        if entity_type not in entity_types
    ]
    if missing_evidence and require_invoice_evidence:
        raise NotInvoiceDocumentError("Document AI no encontro evidencia suficiente de factura")
    result = map_document_ai_result(filename, response.document)
    if missing_evidence:
        result.warnings.insert(
            0,
            "respuesta parcial de Document AI; faltan entidades: " + ", ".join(missing_evidence),
        )
    return result


def extract_uploaded_document(filename: str, data: bytes) -> PocResult:
    """Document AI procesa TODO documento; el tipo se deriva de la evidencia.

    Política (decidida con el negocio): Document AI extrae todos los documentos,
    también OCs y proformas, para máxima calidad de extracción. Una OC/proforma
    NO se vuelve pagable: si Document AI no encuentra evidencia de factura
    (número + total) y el clasificador local la vio como proforma u otro, se
    respeta ese tipo (queda fuera del circuito de pago). Solo se cae al motor
    local si Document AI no está configurado o la API falla.
    """
    local_pdf = read_pdf_bytes(filename, data)
    local_result = extract_document(local_pdf)
    # Texto en memoria para el asistente (el PDF binario nunca sale del entorno).
    local_result.source_text = local_pdf.text
    local_type = local_result.document.get("document_type")

    config = DocumentAIConfig.from_env()
    if config is None:
        local_result.engine = "fallback_local"
        local_result.confidence = min(local_result.confidence, Decimal("0.49"))
        local_result.warnings.insert(0, "Document AI no configurado; resultado local requiere revision")
        return local_result

    try:
        # require_invoice_evidence=False: aprovechamos la extracción de Document
        # AI también en documentos sin número/total de factura (p. ej. OCs), en
        # vez de descartarla. El tipo documental se corrige más abajo.
        managed_result = process_invoice_bytes(
            filename, data, config, require_invoice_evidence=False
        )
        # El texto vectorial local suele preservar mejor etiquetas y guiones
        # de referencias que el OCR administrado. Solo completamos ausencias;
        # nunca reemplazamos una entidad que Document AI haya encontrado.
        for field in ("po_reference", "project_reference"):
            local_value = local_result.document.get(field)
            if managed_result.document.get(field) in (None, "") and local_value:
                managed_result.document[field] = local_value
                managed_result.field_confidences[field] = Decimal("0.95")
        refine_mapped_document(
            managed_result.document,
            local_pdf.text,
            local_result.document,
            managed_result.field_confidences,
        )
        managed_result.source_text = local_pdf.text
        # El parser marca todo como "invoice". Si NO hay evidencia de factura
        # (número + total > 0) y el clasificador local la vio como proforma u
        # otro (p. ej. una OC), respetamos ese tipo para que no entre al circuito
        # de pago. Una factura real conserva su tipo "invoice".
        has_invoice_evidence = bool(managed_result.document.get("numero_factura")) and (
            _money(managed_result.document.get("importe_total")) not in (None, Decimal("0"))
        )
        if not has_invoice_evidence and local_type in ("proforma_or_advance_request", "other"):
            managed_result.document["document_type"] = local_type
            managed_result.field_confidences["document_type"] = (
                local_result.field_confidences.get("document_type", Decimal("0.60"))
            )
        return managed_result
    except NotInvoiceDocumentError:
        return local_result
    except Exception as exc:
        local_result.engine = "fallback_local"
        local_result.confidence = Decimal("0.00")
        local_result.warnings.insert(
            0,
            f"Document AI no disponible ({type(exc).__name__}); resultado local requiere revision",
        )
        return local_result
