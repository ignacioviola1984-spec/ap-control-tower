"""Enmascaramiento de datos sensibles (bancarios) para UI, logs y respuestas.

Regla: los datos bancarios se ALMACENAN completos (son necesarios para el
control C6 de fraude), pero JAMAS se muestran completos fuera de un contexto
autorizado. Estas funciones producen la forma enmascarada; el valor completo
solo se expone por rutas explicitamente autorizadas (definidas en fases de
RBAC posteriores).
"""

from __future__ import annotations

import re


def mask_iban(iban: str | None) -> str | None:
    """Enmascara un IBAN dejando visibles pais + 2 primeros y 4 ultimos.

    'ES9121000418450200051332' -> 'ES91********1332'
    Preserva None y valores demasiado cortos (se enmascaran por completo).
    """
    if iban is None:
        return None
    compact = re.sub(r"\s+", "", iban)
    if len(compact) <= 8:
        return "*" * len(compact)
    visible_head = compact[:4]
    visible_tail = compact[-4:]
    hidden = "*" * (len(compact) - 8)
    return f"{visible_head}{hidden}{visible_tail}"


def mask_account(account: str | None) -> str | None:
    """Enmascara una cuenta local / CCC dejando visibles los 4 ultimos digitos."""
    if account is None:
        return None
    compact = re.sub(r"\s+", "", account)
    if len(compact) <= 4:
        return "*" * len(compact)
    return "*" * (len(compact) - 4) + compact[-4:]


def mask_tax_id(tax_id: str | None) -> str | None:
    """Enmascara un identificador fiscal dejando visibles los 3 ultimos."""
    if tax_id is None:
        return None
    compact = tax_id.strip()
    if len(compact) <= 3:
        return "*" * len(compact)
    return "*" * (len(compact) - 3) + compact[-3:]
