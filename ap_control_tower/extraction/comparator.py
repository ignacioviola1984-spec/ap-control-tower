"""Comparador extraccion vs etiquetado humano (golden labels).

Semantica de null (los null CUENTAN):
  golden null + extraido null   -> ACIERTO (extraer null donde el humano
                                   etiqueto null es un acierto)
  golden null + extraido valor  -> ALUCINACION (error grave; se reporta por
                                   separado del resto de los errores)
  golden valor + extraido null  -> OMISION
  golden valor + extraido valor -> ACIERTO si coinciden normalizados,
                                   DISCREPANCIA si no

Normalizacion por tipo de campo (FIELD_KINDS): fechas a ISO, importes a
Decimal, ids (IBAN/BIC/NIF/numero) sin espacios ni guiones y en mayusculas,
strings y texto crudo case-insensitive con espacios colapsados.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .schema import FIELD_KINDS, FIELD_ORDER

ACIERTO = "acierto"
ALUCINACION = "alucinacion"
OMISION = "omision"
DISCREPANCIA = "discrepancia"


# ------------------------------------------------------------ normalizacion
def _norm(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    kind = FIELD_KINDS[field_name]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "si", "sí", "yes")
    if kind == "list":
        items = value if isinstance(value, list) else [x for x in str(value).split(";") if x.strip()]
        return tuple(sorted(i.strip().lower() for i in items))
    s = str(value).strip()
    if s == "":
        return None
    if kind == "amount":
        try:
            return Decimal(s.replace(".", "").replace(",", ".")) if ("," in s and "." in s) \
                else Decimal(s.replace(",", "."))
        except InvalidOperation:
            return s.lower()
    if kind == "date":
        return s[:10]
    if kind == "id":
        return "".join(s.split()).replace("-", "").upper() if field_name in ("iban", "bic") \
            else " ".join(s.split()).upper()
    if kind == "enum":
        return s.strip().lower()
    # str / text_raw
    return " ".join(s.split()).casefold()


def _is_null(field_name: str, value: Any) -> bool:
    if field_name == "iban_enmascarado":
        return False  # booleano: siempre comparable, false es un valor
    if field_name == "campos_ilegibles":
        return False  # lista: vacia es un valor comparable
    return value is None or (isinstance(value, str) and value.strip() == "")


# ------------------------------------------------------------ resultados
@dataclass
class FieldResult:
    doc_id: str
    field: str
    outcome: str          # acierto | alucinacion | omision | discrepancia
    golden: Any
    extracted: Any


@dataclass
class ComparisonReport:
    results: list[FieldResult] = field(default_factory=list)

    def _count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    @property
    def aciertos(self) -> int:
        return self._count(ACIERTO)

    @property
    def discrepancias(self) -> list[FieldResult]:
        return [r for r in self.results if r.outcome == DISCREPANCIA]

    @property
    def omisiones(self) -> list[FieldResult]:
        return [r for r in self.results if r.outcome == OMISION]

    @property
    def alucinaciones(self) -> list[FieldResult]:
        """Errores graves: valor inventado donde el humano etiqueto null."""
        return [r for r in self.results if r.outcome == ALUCINACION]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        return self.aciertos / self.total if self.total else 0.0

    @property
    def null_aciertos(self) -> int:
        """Aciertos donde golden era null (extraer null era lo correcto)."""
        return sum(1 for r in self.results
                   if r.outcome == ACIERTO and _is_null(r.field, r.golden))

    def summary(self) -> dict:
        return {
            "campos_comparados": self.total,
            "aciertos": self.aciertos,
            "aciertos_null": self.null_aciertos,
            "discrepancias": len(self.discrepancias),
            "omisiones": len(self.omisiones),
            "alucinaciones": len(self.alucinaciones),
            "accuracy": round(self.accuracy, 4),
        }


# ------------------------------------------------------------ comparacion
def compare_document(doc_id: str, extracted: dict, golden: dict) -> list[FieldResult]:
    results = []
    for f in FIELD_ORDER:
        g, e = golden.get(f), extracted.get(f)
        g_null, e_null = _is_null(f, g), _is_null(f, e)
        if g_null and e_null:
            outcome = ACIERTO
        elif g_null and not e_null:
            outcome = ALUCINACION
        elif not g_null and e_null:
            outcome = OMISION
        else:
            outcome = ACIERTO if _norm(f, g) == _norm(f, e) else DISCREPANCIA
        results.append(FieldResult(doc_id=doc_id, field=f, outcome=outcome,
                                   golden=g, extracted=e))
    return results


def compare_batch(pairs: list[tuple[str, dict, dict]]) -> ComparisonReport:
    """pairs: [(doc_id, extraido, golden), ...] -> reporte agregado."""
    report = ComparisonReport()
    for doc_id, extracted, golden in pairs:
        report.results.extend(compare_document(doc_id, extracted, golden))
    return report


# ------------------------------------------------------------ CSV de labels
def load_labels_csv(path: str | Path) -> dict[str, dict]:
    """Lee un CSV de etiquetado (labels/golden) -> {doc_id: documento}.

    Celda vacia = null. iban_enmascarado: true/false. campos_ilegibles:
    nombres separados por ';'.
    """
    docs: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            doc_id = row.get("doc_id") or row.get("archivo") or f"doc-{len(docs) + 1}"
            doc: dict[str, Any] = {}
            for f in FIELD_ORDER:
                raw = (row.get(f) or "").strip()
                if f == "iban_enmascarado":
                    doc[f] = raw.lower() in ("true", "1", "si", "sí", "yes")
                elif f == "campos_ilegibles":
                    doc[f] = [x.strip() for x in raw.split(";") if x.strip()]
                else:
                    doc[f] = raw if raw != "" else None
            docs[doc_id] = doc
    return docs


def labels_template_row() -> list[str]:
    """Columnas canonicas del labels_template.csv (sincronizadas al esquema)."""
    return ["doc_id", "archivo", *FIELD_ORDER, "notas_etiquetador"]
