"""Validacion local del CUIT argentino (modulo 11). Pura, sin red, gratis.

Corre en todos los modos de operacion (incluso ``AP_ARCA_MODE=off``): un
digito verificador invalido no necesita a ARCA para detectarse.

Regla de alcance, decidida con evidencia del golden dataset: la validacion
solo aplica a valores "candidatos a CUIT" (exactamente 11 digitos tras quitar
separadores). Un CIF/NIF europeo con letras jamas es candidato; los tax id
enmascarados (``******999``) tampoco. Riesgo residual documentado en el
runbook: un IVA extranjero de 11 digitos (p. ej. partita IVA italiana) con
prefijo coincidente podria evaluarse como CUIT.
"""

from __future__ import annotations

_SEPARADORES = str.maketrans("", "", " -./")

# Prefijos de CUIT/CUIL asignados por ARCA.
PREFIJOS_VALIDOS = ("20", "23", "24", "25", "26", "27", "30", "33", "34")

_PESOS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def normalizar(valor) -> str | None:
    """11 digitos sin separadores, o None si el valor no es candidato a CUIT."""
    if valor is None:
        return None
    limpio = str(valor).strip().translate(_SEPARADORES)
    if len(limpio) == 11 and limpio.isdigit():
        return limpio
    return None


def es_cuit_candidato(valor) -> bool:
    return normalizar(valor) is not None


def digito_verificador(diez_digitos: str) -> int | None:
    """Digito verificador modulo 11; None si el resto es 1 (no existe CUIT:
    ARCA lo resuelve cambiando el prefijo, p. ej. 20 -> 23)."""
    suma = sum(int(d) * p for d, p in zip(diez_digitos, _PESOS))
    resto = suma % 11
    if resto == 0:
        return 0
    if resto == 1:
        return None
    return 11 - resto


def cuit_valido(valor) -> bool:
    """Prefijo asignado por ARCA + digito verificador correcto."""
    cuit = normalizar(valor)
    if cuit is None:
        return False
    if cuit[:2] not in PREFIJOS_VALIDOS:
        return False
    esperado = digito_verificador(cuit[:10])
    return esperado is not None and esperado == int(cuit[10])


def generar_cuit_sintetico(indice: int, prefijo: str = "30") -> str:
    """CUIT sintetico VALIDO y deterministico para fixtures de tests.

    ``indice`` distinto -> CUIT distinto. Nunca usar CUITs reales en tests.
    """
    if prefijo not in PREFIJOS_VALIDOS:
        raise ValueError(f"prefijo invalido: {prefijo}")
    # Cada indice recorre su propia decena: dos indices distintos no pueden
    # colisionar, y dentro de una decena siempre existe un verificador valido
    # (los restos avanzan de a 2 modulo 11: a lo sumo un caso cae en resto 1).
    for k in range(10):
        cuerpo = f"{prefijo}{(int(indice) * 10 + k) % 10**8:08d}"
        dv = digito_verificador(cuerpo)
        if dv is not None:
            return f"{cuerpo}{dv}"
    raise AssertionError("inalcanzable: una decena siempre tiene un dv valido")
