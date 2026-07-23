"""Evidence-first extraction for historical AP fields.

These fields are intentionally conservative: a plausible-looking date or the
word ``reg`` is not enough.  Every returned value has an explicit textual
anchor that can be stored in the historical evidence memory.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class HistoricalEvidence:
    field_name: str
    value: str
    evidence: str
    method: str
    confidence: float


@dataclass(frozen=True)
class HistoricalFields:
    proveedor_registro: HistoricalEvidence | None = None
    periodo_servicio_desde: HistoricalEvidence | None = None
    periodo_servicio_hasta: HistoricalEvidence | None = None
    condiciones_pago: HistoricalEvidence | None = None


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").replace("\x00", " "))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


def _clean_line(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _lines(text: str) -> list[str]:
    return [_clean_line(line) for line in str(text or "").splitlines() if _clean_line(line)]


def _evidence(lines: list[str], index: int, *, include_next: bool = False) -> str:
    selected = lines[index:index + (2 if include_next else 1)]
    return " | ".join(selected)[:320]


_REGISTRY_PATTERNS: tuple[tuple[str, re.Pattern[str], object], ...] = (
    (
        "kvk",
        re.compile(r"\bKVK(?:\s*(?:number|nr\.?|nummer))?\s*[:#.]?\s*((?:\d[\s.]*){8})(?=\D|$)", re.I),
        lambda match: f"KVK {re.sub(r'\D', '', match.group(1))}",
    ),
    (
        "siret",
        re.compile(r"\bSIRET\s*[:#.]?\s*((?:\d[\s.]*){14})(?=\D|$)", re.I),
        lambda match: f"SIRET {re.sub(r'\D', '', match.group(1))}",
    ),
    (
        "siren",
        re.compile(r"\bSIREN\s*[:#.]?\s*((?:\d[\s.]*){9})(?=\D|$)", re.I),
        lambda match: f"SIREN {re.sub(r'\D', '', match.group(1))}",
    ),
    (
        "hrb",
        re.compile(r"\bH\s*R\s*B\s*[:#.]?\s*((?:\d[\s.]*){3,10})(?=\D|$)", re.I),
        lambda match: f"HRB {re.sub(r'\D', '', match.group(1))}",
    ),
    (
        "company_registration",
        re.compile(
            r"\bCompany\s+(Registration|Reg(?:istration)?\.?)\s+"
            r"(No\.?|Number)\s*[:#.]?\s*((?=[A-Z0-9-]{5,20}\b)(?=[A-Z0-9-]*\d)[A-Z0-9-]{5,20})\b",
            re.I,
        ),
        lambda match: (
            f"Company Registration No. {match.group(3)}"
            if _fold(match.group(1)).startswith("registration")
            else f"Company Reg No {match.group(3)}"
        ),
    ),
    (
        "company_number",
        re.compile(r"\bCompany\s+(?:No\.?|Number)\s*[:#.]?\s*((?=[A-Z0-9-]{5,20}\b)(?=[A-Z0-9-]*\d)[A-Z0-9-]{5,20})\b", re.I),
        lambda match: f"Company No. {match.group(1)}",
    ),
)


def extract_registry(text: str) -> HistoricalEvidence | None:
    lines = _lines(text)
    for index, line in enumerate(lines):
        for method, pattern, formatter in _REGISTRY_PATTERNS:
            match = pattern.search(line)
            if match:
                value = formatter(match)  # type: ignore[operator]
                return HistoricalEvidence(
                    "proveedor_registro", value, _evidence(lines, index), method, 0.99,
                )
    return None


_MONTHS = {
    "jan": 1, "january": 1, "enero": 1, "janvier": 1,
    "feb": 2, "february": 2, "febrero": 2, "fevrier": 2,
    "mar": 3, "march": 3, "marzo": 3, "mars": 3,
    "apr": 4, "april": 4, "abril": 4, "avril": 4,
    "may": 5, "mayo": 5, "mai": 5,
    "jun": 6, "june": 6, "junio": 6, "juin": 6,
    "jul": 7, "july": 7, "julio": 7, "juillet": 7,
    "aug": 8, "august": 8, "agosto": 8, "aout": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9, "septembre": 9,
    "oct": 10, "october": 10, "octubre": 10, "octobre": 10,
    "nov": 11, "november": 11, "noviembre": 11, "novembre": 11,
    "dec": 12, "december": 12, "diciembre": 12, "decembre": 12,
}
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_TOKEN = (
    rf"(?:\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}|"
    rf"\d{{1,2}}[-/.]\d{{1,2}}[-/.](?:\d{{4}}|\d{{2}})|"
    rf"\d{{1,2}}\s+(?:{_MONTH_WORDS})\s+\d{{4}}|"
    rf"(?:{_MONTH_WORDS})\s+\d{{1,2}},?\s+\d{{4}})"
)
_DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})\s*(?:(?:a|al|to|through|hasta)\s*:?\s*|[-–—]\s*)(?P<end>{_DATE_TOKEN})",
    re.I,
)
_SINGLE_DATE_RE = re.compile(rf"(?P<value>{_DATE_TOKEN})", re.I)
_LOOSE_TEXTUAL_DATE_RE = re.compile(
    rf"\b(?P<month>{_MONTH_WORDS})\s+(?P<day>\d{{1,2}}),?"
    rf"(?P<middle>.{{0,36}}?)(?P<year>20\d{{2}})\b",
    re.I,
)
_MONTH_YEAR_RE = re.compile(rf"\b(?P<month>{_MONTH_WORDS})\s+(?P<year>20\d{{2}})\b", re.I)
_SERVICE_LABELS = (
    "periodo de servicio", "periodo servicio", "periodo desde", "periodo facturado",
    "periodo de facturacion", "periodo facturacion", "periodo de consumo",
    "service period", "period of service", "billing period", "billing cycle",
    "consumption period", "date of service", "service date", "fecha de servicio",
    "fecha servicio", "delivery date", "leveringsdatum", "cuota",
    "monthly service", "subscription period", "services provision", "service provision",
)
_SINGLE_SERVICE_LABELS = (
    "date of service", "service date", "fecha de servicio", "fecha servicio",
    "delivery date", "leveringsdatum",
)
_DATE_TABLE_LABEL_RE = re.compile(
    r"invoice\s+date|date\s+of\s+service|service\s+date|payment\s+due\s+date|"
    r"fecha\s+(?:de\s+)?factura|fecha\s+(?:de\s+)?servicio|fecha\s+de\s+vencimiento",
    re.I,
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_date(raw: str, *, month_first: bool = False) -> date | None:
    value = _fold(raw).strip(" ,.;")
    iso = re.fullmatch(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
    if iso:
        return _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    numeric = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})", value)
    if numeric:
        year = int(numeric.group(3))
        year += 2000 if year < 100 else 0
        first, second = int(numeric.group(1)), int(numeric.group(2))
        if month_first and first <= 12:
            return _safe_date(year, first, second)
        return _safe_date(year, second, first)
    european = re.fullmatch(rf"(\d{{1,2}})\s+({_MONTH_WORDS})\s+(20\d{{2}})", value)
    if european:
        return _safe_date(int(european.group(3)), _MONTHS[european.group(2)], int(european.group(1)))
    american = re.fullmatch(rf"({_MONTH_WORDS})\s+(\d{{1,2}}),?\s+(20\d{{2}})", value)
    if american:
        return _safe_date(int(american.group(3)), _MONTHS[american.group(1)], int(american.group(2)))
    return None


def _service_date_from_split_table(lines: list[str], index: int) -> date | None:
    """Map a service-date column to the value in the following table row."""
    if index + 1 >= len(lines):
        return None
    labels = [
        _fold(match.group(0))
        for match in _DATE_TABLE_LABEL_RE.finditer(_fold(lines[index]))
    ]
    service_indexes = [
        position
        for position, label in enumerate(labels)
        if label in {"date of service", "service date", "fecha de servicio", "fecha servicio"}
    ]
    if len(labels) < 2 or len(service_indexes) != 1:
        return None
    date_tokens = [
        match.group("value")
        for match in _SINGLE_DATE_RE.finditer(_fold(lines[index + 1]))
    ]
    service_index = service_indexes[0]
    if len(date_tokens) < len(labels) or service_index >= len(date_tokens):
        return None
    return _parse_date(date_tokens[service_index])


def extract_service_period(text: str) -> tuple[HistoricalEvidence | None, HistoricalEvidence | None]:
    lines = _lines(text)
    for index, line in enumerate(lines):
        folded = _fold(line)
        label = next((item for item in _SERVICE_LABELS if item in folded), None)
        if label == "cuota" and not re.search(rf"(?i)^\s*cuota\s+({_MONTH_WORDS})\s+20\d{{2}}\b", _fold(line)):
            label = None
        if not label:
            continue
        window_end = min(len(lines), index + 6)
        joined = " ".join(lines[index:window_end])
        include_next = window_end > index + 1
        joined_folded = _fold(joined)
        month_first = " from " in f" {joined_folded} " and " to " in f" {joined_folded} "
        date_range = _DATE_RANGE_RE.search(_fold(joined))
        if date_range:
            start = _parse_date(date_range.group("start"), month_first=month_first)
            end = _parse_date(date_range.group("end"), month_first=month_first)
            if start and end and start <= end:
                snippet = " | ".join(lines[index:window_end])[:320]
                return (
                    HistoricalEvidence("periodo_servicio_desde", start.isoformat(), snippet, "labelled_date_range", 0.97),
                    HistoricalEvidence("periodo_servicio_hasta", end.isoformat(), snippet, "labelled_date_range", 0.97),
                )
        if label in _SINGLE_SERVICE_LABELS:
            parsed = _service_date_from_split_table(lines, index)
            single = _SINGLE_DATE_RE.search(_fold(joined))
            if parsed is None:
                parsed = _parse_date(single.group("value"), month_first=month_first) if single else None
            if parsed:
                snippet = " | ".join(lines[index:window_end])[:320]
                item = HistoricalEvidence(
                    "periodo_servicio_desde", parsed.isoformat(), snippet, "labelled_service_date", 0.96,
                )
                return item, HistoricalEvidence(
                    "periodo_servicio_hasta", parsed.isoformat(), snippet, "labelled_service_date", 0.96,
                )
        loose_dates: list[date] = []
        for loose in _LOOSE_TEXTUAL_DATE_RE.finditer(joined_folded):
            parsed = _safe_date(
                int(loose.group("year")),
                _MONTHS[loose.group("month")],
                int(loose.group("day")),
            )
            if parsed and parsed not in loose_dates:
                loose_dates.append(parsed)
        if len(loose_dates) >= 2 and loose_dates[0] <= loose_dates[1]:
            snippet = " | ".join(lines[index:window_end])[:320]
            return (
                HistoricalEvidence("periodo_servicio_desde", loose_dates[0].isoformat(), snippet, "labelled_table_range", 0.92),
                HistoricalEvidence("periodo_servicio_hasta", loose_dates[1].isoformat(), snippet, "labelled_table_range", 0.92),
            )
        month_year = _MONTH_YEAR_RE.search(_fold(joined))
        if month_year:
            month = _MONTHS[month_year.group("month")]
            year = int(month_year.group("year"))
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])
            snippet = " | ".join(lines[index:window_end])[:320]
            return (
                HistoricalEvidence("periodo_servicio_desde", start.isoformat(), snippet, "labelled_month", 0.94),
                HistoricalEvidence("periodo_servicio_hasta", end.isoformat(), snippet, "labelled_month", 0.94),
            )
    return None, None


_PAYMENT_TERMS_LABEL_RE = re.compile(
    r"(?:payment\s+terms?|terms\s+of\s+payment|\bterms|condiciones?\s+de\s+pago|vencimiento)\s*[:#-]?\s*(.*)$",
    re.I,
)
_PAYMENT_METHOD_LABEL_RE = re.compile(
    r"(?:forma\s+(?:de\s+)?pago|modo\s+de\s+pago|tipo\s+de\s+pago)\s*[:#-]?\s*(.*)$",
    re.I,
)
_PAYMENT_PHRASES: tuple[re.Pattern[str], ...] = (
    re.compile(r"R\.?\s*SEPA\s+a\s+\d{1,3}\s+d[ií]as?(?:\s*,?\s*d[ií]as?\s+fijos?\s+de\s+pago\s+\d{1,2}\s+y\s+\d{1,2})?", re.I),
    re.compile(r"Transferencia\s+bancaria(?:\s*,?\s*Contado|\s+a\s+\d{1,3}\s+d[ií]as?(?:\s+de\s+la\s+recepci[oó]n\s+de\s+la\s+factura)?)", re.I),
    re.compile(r"Payment\s+to\s+be\s+made\s+within\s+\d{1,3}\s+days?", re.I),
    re.compile(r"payable\s+within\s+\d{1,3}\s+days?", re.I),
    re.compile(r"\d{1,3}\s+days?\s+from\s+invoice\s+issuance", re.I),
    re.compile(r"\d{1,3}\s+days?\s+end\s+of\s+month", re.I),
    re.compile(r"Net\s+\d{1,3}\b", re.I),
    re.compile(r"Due\s+Upon\s+Receipt", re.I),
    re.compile(r"PAYABLE\s*:\s*IMMEDIATELY|payable\s+immediately", re.I),
    re.compile(r"CASH\s+PAYMENT", re.I),
    re.compile(r"Un\s+plazo\s+a\s+\d{1,3}\s+d[ií]as?", re.I),
    re.compile(r"en\s+el\s+plazo\s+de\s+\d{1,3}\s+d[ií]as?", re.I),
    re.compile(r"\d{1,3}\s+d[ií]as?\s+(?:f\.?\s*f\.?|fin\s+de\s+mes)", re.I),
)
_LABELLED_PAYMENT_PHRASES: tuple[re.Pattern[str], ...] = (
    re.compile(r"within\s+\d{1,3}\s+days?", re.I),
    re.compile(r"\d{1,3}\s+(?:days?|d[ií]as?)\b", re.I),
    re.compile(r"Recibo\s+Domiciliado", re.I),
    re.compile(r"Recibo\s+Contado", re.I),
    re.compile(r"Pago\s+inmediato", re.I),
)


def _clean_payment_value(value: str, *, labelled: bool = False) -> str | None:
    cleaned = _clean_line(value).strip(" |;,.:-")
    if not cleaned or len(cleaned) > 140 or len(cleaned) < 2:
        return None
    folded = _fold(cleaned)
    if any(token in folded for token in ("http://", "https://", "proteccion de datos", "privacy policy")):
        return None
    for pattern in _PAYMENT_PHRASES:
        match = pattern.search(cleaned)
        if match:
            return _clean_line(match.group(0)).strip(" .")
    if labelled:
        for pattern in _LABELLED_PAYMENT_PHRASES:
            match = pattern.search(cleaned)
            if match:
                return _clean_line(match.group(0)).strip(" .")
    if labelled and folded in {"rc", "contado", "en la forma establecida"}:
        return cleaned
    return None


def extract_payment_terms(text: str) -> HistoricalEvidence | None:
    lines = _lines(text)
    for label_pattern in (_PAYMENT_TERMS_LABEL_RE, _PAYMENT_METHOD_LABEL_RE):
        for index, line in enumerate(lines):
            labelled_match = label_pattern.search(line)
            if not labelled_match:
                continue
            candidate = _clean_payment_value(labelled_match.group(1), labelled=True)
            include_next = False
            if candidate is None and index + 1 < len(lines):
                candidate = _clean_payment_value(lines[index + 1], labelled=True)
                include_next = candidate is not None
            if candidate:
                return HistoricalEvidence(
                    "condiciones_pago", candidate,
                    _evidence(lines, index, include_next=include_next),
                    "explicit_payment_label", 0.97,
                )
    for index, line in enumerate(lines):
        candidate = _clean_payment_value(line)
        if candidate is None and re.search(r"(?i)\b(?:transfer|payment|pay|invoice)\b", line):
            generic_within = re.search(r"(?i)\bwithin\s+\d{1,3}\s+days?\b", line)
            candidate = _clean_line(generic_within.group(0)) if generic_within else None
        if candidate:
            return HistoricalEvidence(
                "condiciones_pago", candidate, _evidence(lines, index),
                "explicit_payment_phrase", 0.92,
            )
    return None


def extract_historical_fields(text: str) -> HistoricalFields:
    start, end = extract_service_period(text)
    return HistoricalFields(
        proveedor_registro=extract_registry(text),
        periodo_servicio_desde=start,
        periodo_servicio_hasta=end,
        condiciones_pago=extract_payment_terms(text),
    )
