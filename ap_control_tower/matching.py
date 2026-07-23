"""Utilidades compartidas para comparaciones fuzzy controladas.

El umbral vive en un solo lugar. Los consumidores normalizan sus valores antes
de llamar estas funciones; este modulo solo calcula la similitud.
"""

from __future__ import annotations

from difflib import SequenceMatcher


FUZZY_SIMILARITY_THRESHOLD = 0.85


def similarity_score(left: str, right: str) -> float:
    """Devuelve una similitud determinista en el intervalo ``[0, 1]``."""
    return SequenceMatcher(None, str(left or ""), str(right or "")).ratio()


def meets_fuzzy_threshold(left: str, right: str) -> bool:
    """Indica si dos valores alcanzan el umbral canonico compartido."""
    return similarity_score(left, right) >= FUZZY_SIMILARITY_THRESHOLD
