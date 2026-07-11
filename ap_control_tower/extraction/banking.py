"""Extraccion y validacion conservadora de datos bancarios visibles."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


IBAN_LENGTHS = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18,
    "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22,
    "IS": 26, "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21,
    "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
    "RO": 24, "SE": 24, "SI": 19, "SK": 24,
}

ISO_COUNTRY_CODES = set(IBAN_LENGTHS) | {
    "AE", "AL", "AR", "AU", "BA", "BR", "CA", "CL", "CN", "CO",
    "CR", "DO", "DZ", "EG", "GE", "GI", "HK", "IL", "IN", "JP",
    "KR", "KW", "KZ", "LB", "MA", "ME", "MK", "MX", "MU", "NZ",
    "PK", "QA", "RS", "SA", "SG", "SM", "TN", "TR", "UA", "US",
    "UY", "VA", "VG", "ZA",
}

IBAN_LINE_RE = re.compile(
    r"\bIBAN\s*[:#-]?\s*([A-Z]{2}\d{2}[A-Z0-9* .-]{8,48})",
    re.IGNORECASE,
)
BIC_LINE_RE = re.compile(
    r"\b(?:BIC|SWIFT(?:\s*/\s*BIC)?)\s*[:#-]?\s*([A-Z0-9]{8}(?:[A-Z0-9]{3})?)\b",
    re.IGNORECASE,
)
CCC_RE = re.compile(r"\b(\d{4}[- ]\d{4}[- ]\d{2}[- ]\d{10})\b")


@dataclass(frozen=True)
class BankDetails:
    iban: str | None = None
    iban_enmascarado: bool = False
    bic: str | None = None
    banco: str | None = None
    cuenta: str | None = None


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9*]", "", value.upper())


def is_valid_iban(value: str) -> bool:
    compact = _compact(value)
    if "*" in compact or len(compact) < 4:
        return False
    expected = IBAN_LENGTHS.get(compact[:2])
    if expected is None or len(compact) != expected or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    return int(numeric) % 97 == 1


def is_valid_bic(value: str) -> bool:
    compact = _compact(value)
    if len(compact) not in (8, 11):
        return False
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?", compact):
        return False
    return compact[4:6] in ISO_COUNTRY_CODES


def is_valid_spanish_ccc(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 20:
        return False

    def control(block: str, weights: tuple[int, ...]) -> int:
        digit = 11 - (sum(int(n) * w for n, w in zip(block, weights)) % 11)
        if digit == 11:
            return 0
        if digit == 10:
            return 1
        return digit

    first = control(digits[:8], (4, 8, 5, 10, 9, 7, 3, 6))
    second = control(digits[10:], (1, 2, 4, 8, 5, 10, 9, 7, 3, 6))
    return digits[8:10] == f"{first}{second}"


def _extract_iban(text: str, preferred: str | None = None) -> tuple[str | None, bool]:
    candidates = [preferred] if preferred else []
    candidates.extend(match.group(1).strip() for match in IBAN_LINE_RE.finditer(text))
    candidates.extend(
        match.group(0).strip()
        for match in re.finditer(r"\b[A-Z]{2}\d{2}(?:[ .-]?[A-Z0-9]){11,30}\b", text, re.IGNORECASE)
    )
    for raw in candidates:
        if not raw:
            continue
        compact = _compact(raw)
        country_length = IBAN_LENGTHS.get(compact[:2])
        if country_length and len(compact) >= country_length:
            compact = compact[:country_length]
        if "*" in compact:
            if compact[:2] in IBAN_LENGTHS and 15 <= len(compact) <= 34:
                return raw.strip(" .,;:-"), True
            continue
        if is_valid_iban(compact):
            return compact, False
    return None, False


def _extract_bic(text: str) -> str | None:
    for match in BIC_LINE_RE.finditer(text):
        candidate = _compact(match.group(1))
        if is_valid_bic(candidate):
            return candidate
    return None


def _extract_local_account(text: str) -> str | None:
    for match in CCC_RE.finditer(text):
        candidate = match.group(1)
        if is_valid_spanish_ccc(candidate):
            return candidate

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"(?i)\b(?:numero de cuenta|n[úu]mero de cuenta|cuenta)\b", line):
            continue
        window = " ".join(lines[index:index + 2])
        masked = re.search(r"([*Xx.]{4,}[*Xx. 0-9-]{4,}\d{2,})", window)
        if masked:
            return re.sub(r"\s+", " ", masked.group(1)).strip()

    account_no = re.search(
        r"(?i)\bbank account\b.*?\bNo\.?\s*[:#-]?\s*([A-Z0-9-]{6,34})",
        text,
    )
    return account_no.group(1) if account_no else None


def _extract_bank_name(text: str, account: str | None) -> str | None:
    if account:
        escaped = re.escape(account)
        after_account = re.search(rf"{escaped}\s*\(([^)]+)\)", text, re.IGNORECASE)
        if after_account:
            return after_account.group(1).strip()

    patterns = (
        r"(?im)^\s*Bank Name\s*[:#-]\s*(.+?)\s*$",
        r"(?i)\bbank account\s*:\s*(.+?)(?:,\s*No\.?\s*:|\n|$)",
        r"(?i)\((BANCO\s+[^)]+)\)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" .,;:-")
    return None


def extract_bank_details(text: str, preferred_iban: str | None = None) -> BankDetails:
    iban, masked = _extract_iban(text, preferred_iban)
    bic = _extract_bic(text)
    account = _extract_local_account(text)
    bank = _extract_bank_name(text, account)
    return BankDetails(
        iban=iban,
        iban_enmascarado=masked,
        bic=bic,
        banco=bank,
        cuenta=account,
    )


def bank_evidence_present(text: str) -> bool:
    folded = _fold(text)
    return any(term in folded for term in (
        "iban", "bic", "swift", "bank account", "bank name",
        "numero de cuenta", "cuenta bancaria", "detalles de pago",
    ))
