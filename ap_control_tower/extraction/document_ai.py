"""Google Document AI Invoice Parser adapter for uploaded PDFs.

The managed parser is the source of truth for invoice entities and layout.
The deterministic extractor remains useful for classification, references and
an explicit degraded-mode result when the managed service is unavailable.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

from .banking import (
    bank_evidence_present,
    extract_bank_details,
    is_valid_bic,
    is_valid_iban,
    is_valid_spanish_ccc,
)
from .pdf_poc import PdfText, PocResult, extract_document, read_pdf_bytes
from .schema import validate_document
from .tax_id import tax_id_warning


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
    r"\b(?:S\.?\s*L\.?\s*(?:U|L)?\.?|S\.?\s*A\.?|B\.?\s*V\.?|"
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
    value = re.sub(r"(?i)\b(?:EUR|USD|GBP)\b|[€$£%]", "", raw).strip()
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
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if rate:
        return format(number.normalize(), "f")
    return f"{number.quantize(Decimal('0.01'))}"


def _date_value(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", raw)
    return match.group(0) if match else raw.strip()


def _currency_value(raw: str | None) -> str | None:
    if not raw:
        return None
    folded = raw.strip().upper()
    return {"€": "EUR", "$": "USD", "£": "GBP"}.get(folded, folded[:3])


def _clean_party_name(value: str) -> str:
    cleaned = re.sub(
        r"(?i)^\s*(?:supplier|client|cliente|nombre|account holder|address)\s*[:#-]?\s*",
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


def _is_bad_party_name(value: str | None) -> bool:
    if not value:
        return True
    folded = _fold(value)
    return folded in {
        "grupo", "numero de cliente", "cliente", "client", "supplier", "proveedor",
    } or any(term in folded for term in ("bank account", "account holder", "numero factura"))


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
    if labelled_supplier:
        supplier = labelled_supplier
        confidences["proveedor_nombre_comercial"] = Decimal("0.95")
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
        doc["proveedor_razon_social_legal"] = supplier
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


def _payment_method(text: str, has_bank_details: bool) -> str:
    folded = _fold(text)
    if re.search(r"\b(direct debit|domiciliacion|domiciliado|sepa|cargo en cuenta)\b", folded):
        return "domiciliacion_direct_debit"
    if re.search(r"\b(tarjeta|card payment|credit card|visa|mastercard)\b", folded):
        return "tarjeta"
    if has_bank_details or re.search(r"\b(transferencia|bank transfer|wire transfer)\b", folded):
        return "transferencia"
    return "no_indicado"


def _tax_treatment(doc: dict, text: str) -> str | None:
    folded = _fold(text)
    if any(term in folded for term in (
        "reverse charge", "reverse-charged", "inversion del sujeto pasivo",
        "article 196", "articulo 196",
    )):
        return "intracomunitario_inversion_sujeto_pasivo"
    if doc.get("tipo_iva") is not None or doc.get("importe_iva") is not None:
        return "nacional"
    if any(term in folded for term in ("exempt", "exento", "exonerado")):
        return "exento_otro"
    return None


def _validate_invoice(doc: dict, text: str, confidences: dict[str, Decimal]) -> list[str]:
    warnings: list[str] = []
    missing = [field for field in INVOICE_CRITICAL_FIELDS if doc.get(field) in (None, "")]
    if missing:
        warnings.append("campos criticos ausentes: " + ", ".join(missing))

    supplier = _fold(doc.get("proveedor_nombre_comercial") or "")
    receiver = _fold(doc.get("cliente_nombre") or "")
    if supplier and receiver and supplier == receiver:
        warnings.append("proveedor y cliente no pueden ser la misma entidad")

    try:
        net = Decimal(str(doc["importe_neto"]))
        tax = Decimal(str(doc["importe_iva"]))
        total = Decimal(str(doc["importe_total"]))
        if abs((net + tax) - total) > Decimal("0.02"):
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

    # Un identificador fiscal con un caracter cambiado pasa cualquier revision
    # visual y termina en un pago mal imputado: tiene que saltar solo.
    for tax_field in ("proveedor_tax_id", "cliente_tax_id"):
        tax_warning = tax_id_warning(tax_field, doc.get(tax_field))
        if tax_warning:
            warnings.append(tax_warning)

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
    if due_entity is not None and _entity_mention(due_entity):
        doc["fecha_vencimiento_texto"] = _entity_mention(due_entity)
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
    _set_entity(doc, confidences, entities, "po_reference", ("purchase_order",))
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
    _reconcile_tax(doc, text, confidences)
    has_bank = any((bank.iban, bank.bic, bank.banco, bank.cuenta))
    doc["metodo_pago"] = _payment_method(text, has_bank)
    doc["tratamiento_iva"] = _tax_treatment(doc, text)
    confidences.setdefault("metodo_pago", Decimal("0.85"))
    if doc["tratamiento_iva"]:
        confidences.setdefault("tratamiento_iva", Decimal("0.90"))

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


def process_invoice_bytes(filename: str, data: bytes, config: DocumentAIConfig) -> PocResult:
    from google.cloud import documentai_v1 as documentai  # type: ignore

    client = _document_ai_client(config)
    name = client.processor_path(config.project_id, config.location, config.processor_id)
    request = documentai.ProcessRequest(
        name=name,
        raw_document=documentai.RawDocument(content=data, mime_type="application/pdf"),
    )
    response = client.process_document(request=request, timeout=90)
    entity_types = {
        getattr(entity, "type_", None) or getattr(entity, "type", None)
        for entity in getattr(response.document, "entities", ()) or ()
    }
    if "invoice_id" not in entity_types or "total_amount" not in entity_types:
        raise NotInvoiceDocumentError("Document AI no encontro evidencia suficiente de factura")
    return map_document_ai_result(filename, response.document)


def extract_uploaded_document(filename: str, data: bytes) -> PocResult:
    """Classify locally, then route invoices through the managed parser."""
    local_pdf = read_pdf_bytes(filename, data)
    local_result = extract_document(local_pdf)
    document_type = local_result.document["document_type"]
    folded_name = _fold(filename)
    explicit_non_invoice = document_type == "proforma_or_advance_request" or any(
        term in folded_name for term in ("orden de compra", "purchase order", "pedido", " oc ")
    )
    if explicit_non_invoice:
        return local_result

    config = DocumentAIConfig.from_env()
    if config is None:
        local_result.engine = "fallback_local"
        local_result.confidence = min(local_result.confidence, Decimal("0.49"))
        local_result.warnings.insert(0, "Document AI no configurado; resultado local requiere revision")
        return local_result

    try:
        managed_result = process_invoice_bytes(filename, data, config)
        # El texto vectorial local suele preservar mejor etiquetas y guiones
        # de referencias que el OCR administrado. Solo completamos ausencias;
        # nunca reemplazamos una entidad que Document AI haya encontrado.
        for field in ("po_reference", "project_reference"):
            local_value = local_result.document.get(field)
            if managed_result.document.get(field) in (None, "") and local_value:
                managed_result.document[field] = local_value
                managed_result.field_confidences[field] = Decimal("0.95")
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
