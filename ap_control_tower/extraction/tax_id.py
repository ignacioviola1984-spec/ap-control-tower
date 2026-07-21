"""Checksum de identificadores fiscales espanoles (NIF, NIE, CIF).

Motivacion: un motor de extraccion puede devolver un identificador que "parece
bien" pero tiene un caracter cambiado. Medido el 2026-07-21 sobre facturas
reales, un modelo local devolvio 8 de 8 identificadores de proveedor
incorrectos, y el modo de fallo mas peligroso fue duplicar la letra inicial
(``BB2345674`` por ``B12345674``): un solo caracter, invisible en una revision
visual, que termina en un pago mal imputado. Tiene que saltar solo como
warning, venga del motor que venga.

Reglas implementadas:
    NIF persona fisica  8 digitos + letra de control (modulo 23)
    NIE                 X/Y/Z + 7 digitos + letra (X->0, Y->1, Z->2, modulo 23)
    CIF entidad         letra + 7 digitos + control (digito o letra segun la
                        letra inicial; A/B/E/H exigen digito, K/P/Q/R/S/N/W
                        exigen letra, el resto acepta cualquiera de los dos)

Se acepta el prefijo de IVA intracomunitario ``ES``.
"""

from __future__ import annotations

import re

_NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_CONTROL_LETTERS = "JABCDEFGHI"
_NIE_PREFIX = {"X": "0", "Y": "1", "Z": "2"}

_CIF_DIGIT_ONLY = set("ABEH")      # el control tiene que ser digito
_CIF_LETTER_ONLY = set("KPQRSNW")  # el control tiene que ser letra

_RE_NIF = re.compile(r"^\d{8}[A-Z]$")
_RE_NIE = re.compile(r"^[XYZ]\d{7}[A-Z]$")
_RE_CIF = re.compile(r"^[A-HJ-NP-SUVW]\d{7}[0-9A-J]$")


def normalize_tax_id(value: str) -> str:
    """Compacta y saca el prefijo ES de VAT intracomunitario."""
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    if len(compact) == 11 and compact.startswith("ES"):
        return compact[2:]
    return compact


def _nif_letter(number: int) -> str:
    return _NIF_LETTERS[number % 23]


def is_valid_nif(value: str) -> bool:
    compact = normalize_tax_id(value)
    if not _RE_NIF.fullmatch(compact):
        return False
    return compact[8] == _nif_letter(int(compact[:8]))


def is_valid_nie(value: str) -> bool:
    compact = normalize_tax_id(value)
    if not _RE_NIE.fullmatch(compact):
        return False
    return compact[8] == _nif_letter(int(_NIE_PREFIX[compact[0]] + compact[1:8]))


def is_valid_cif(value: str) -> bool:
    compact = normalize_tax_id(value)
    if not _RE_CIF.fullmatch(compact):
        return False
    body, control = compact[1:8], compact[8]

    total = 0
    for index, char in enumerate(body):
        digit = int(char)
        if index % 2 == 0:              # posiciones impares (1-indexadas): se duplican
            doubled = digit * 2
            total += doubled // 10 + doubled % 10
        else:
            total += digit
    expected_digit = (10 - total % 10) % 10
    expected_letter = _CIF_CONTROL_LETTERS[expected_digit]

    initial = compact[0]
    if initial in _CIF_DIGIT_ONLY:
        return control == str(expected_digit)
    if initial in _CIF_LETTER_ONLY:
        return control == expected_letter
    return control in (str(expected_digit), expected_letter)


def is_valid_spanish_tax_id(value: str) -> bool:
    """True si el identificador supera el checksum de NIF, NIE o CIF."""
    if not value:
        return False
    return is_valid_nif(value) or is_valid_nie(value) or is_valid_cif(value)


def looks_spanish_tax_id(value: str) -> bool:
    """True si el valor tiene la forma de un identificador fiscal espanol.

    Se usa para no castigar identificadores extranjeros: solo se valida el
    checksum cuando el valor podria ser espanol. Un NIF/NIE/CIF compacto tiene
    exactamente 9 caracteres alfanumericos (11 con el prefijo ES), asi que un
    ``DE123456789`` o un ``FR12345678901`` quedan fuera.
    """
    if not value:
        return False
    return len(normalize_tax_id(value)) == 9


_MIN_DIGITOS = 5


def tiene_forma_de_identificador(value: str) -> bool:
    """Un identificador fiscal, de cualquier pais, tiene digitos.

    Medido el 2026-07-21: un motor local devolvio como proveedor_tax_id una
    palabra suelta y una razon social. No fallan ningun checksum porque ni
    siquiera son identificadores; hay que rechazarlos por forma antes de
    intentar validarlos.
    """
    if not value:
        return False
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    return sum(ch.isdigit() for ch in compact) >= _MIN_DIGITOS


def tax_id_warning(field: str, value: str | None) -> str | None:
    """Warning listo para el pipeline, o None si no hay nada que reportar."""
    if not value or not str(value).strip():
        return None
    value = str(value)

    if not tiene_forma_de_identificador(value):
        return (
            f"{field}: '{value}' no tiene forma de identificador fiscal "
            f"(menos de {_MIN_DIGITOS} digitos)"
        )

    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    if compact.startswith("ES") and len(compact) != 11:
        return (
            f"{field}: '{value}' declara prefijo ES pero no tiene la longitud "
            f"de un NIF/CIF espanol (ES + 9 caracteres)"
        )

    if not looks_spanish_tax_id(value):
        return None          # identificador extranjero plausible: no se juzga
    if is_valid_spanish_tax_id(value):
        return None
    return (
        f"{field}: '{value}' no supera el checksum de NIF/CIF espanol "
        f"(digito de control incorrecto)"
    )
