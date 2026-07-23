"""Read-only access to the private historical AP evidence memory.

The database is supplied at runtime through ``AP_EVIDENCE_MEMORY_PATH`` and is
never part of the repository or container image. Transaction-specific facts
are reused only for the exact same document hash. Across documents, the sole
allowed enrichment is a unique, verified supplier registry identifier matched
by exact Tax ID or strongly normalized supplier name.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .sage.vendor_master import normalize_supplier_name


EXACT_DOCUMENT_FIELDS = (
    "proveedor_nombre_comercial",
    "proveedor_registro",
    "periodo_servicio_desde",
    "periodo_servicio_hasta",
    "condiciones_pago",
)


@dataclass(frozen=True)
class MemoryEnrichment:
    field_name: str
    value: str | None
    action: str
    corpus_document_id: str
    page_number: int | None
    confidence: Decimal


def _normalize_tax_id(value: str | None) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    normalized = re.sub(r"^(?:CIF|NIF|DNI|VAT)", "", normalized)
    return normalized[2:] if normalized.startswith("ES") and len(normalized) == 11 else normalized


class HistoricalEvidenceMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError("La memoria histórica configurada no existe.")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def enrich_result(self, result, data: bytes) -> list[MemoryEnrichment]:
        digest = hashlib.sha256(data).hexdigest()
        document = result.document
        applied: list[MemoryEnrichment] = []
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT e.field_name, e.value, e.page_number, e.confidence,
                       e.review_status, d.corpus_document_id
                  FROM field_evidence e
                  JOIN documents d USING(document_sha256)
                 WHERE e.document_sha256 = ?
                   AND e.field_name IN (?,?,?,?,?)
                """,
                (digest, *EXACT_DOCUMENT_FIELDS),
            ).fetchall()
            for row in rows:
                field_name = row["field_name"]
                status = row["review_status"]
                value = row["value"] or None
                current = document.get(field_name)
                action: str | None = None
                if status == "verified_ground_truth" and value and current != value:
                    document[field_name] = value
                    action = "corrected" if current not in (None, "") else "filled"
                elif status == "verified_absent" and current not in (None, ""):
                    document[field_name] = None
                    action = "cleared_by_verified_absence"
                elif status in {"auto_candidate", "model_corroborated"} and value and current in (None, ""):
                    document[field_name] = value
                    action = (
                        "filled_from_model_corroborated_evidence"
                        if status == "model_corroborated"
                        else "filled_from_exact_document_evidence"
                    )
                if action:
                    confidence = Decimal(str(row["confidence"])).quantize(Decimal("0.01"))
                    result.field_confidences[field_name] = confidence
                    applied.append(MemoryEnrichment(
                        field_name=field_name,
                        value=value,
                        action=action,
                        corpus_document_id=row["corpus_document_id"],
                        page_number=row["page_number"],
                        confidence=confidence,
                    ))

            if not rows and document.get("proveedor_registro") in (None, ""):
                registry = self._unique_verified_registry(connection, document)
                if registry:
                    document["proveedor_registro"] = registry["value"]
                    confidence = Decimal("0.98")
                    result.field_confidences["proveedor_registro"] = confidence
                    applied.append(MemoryEnrichment(
                        field_name="proveedor_registro",
                        value=registry["value"],
                        action="filled_from_unique_verified_supplier_fact",
                        corpus_document_id=registry["corpus_document_id"],
                        page_number=registry["page_number"],
                        confidence=confidence,
                    ))

        for item in applied:
            page = f", página {item.page_number}" if item.page_number else ""
            result.warnings.append(
                "FYI memoria histórica: "
                f"{item.field_name} {item.action} con evidencia {item.corpus_document_id}{page}."
            )
        return applied

    def _unique_verified_registry(self, connection: sqlite3.Connection, document: dict):
        target_tax = _normalize_tax_id(document.get("proveedor_tax_id"))
        target_name = normalize_supplier_name(document.get("proveedor_nombre_comercial"))
        if not target_tax and not target_name:
            return None
        rows = connection.execute(
            """
            SELECT e.value, e.page_number, d.corpus_document_id,
                   d.supplier_tax_id, d.supplier_name
              FROM field_evidence e
              JOIN documents d USING(document_sha256)
             WHERE e.field_name = 'proveedor_registro'
               AND e.review_status = 'verified_ground_truth'
               AND COALESCE(e.value, '') <> ''
            """
        ).fetchall()
        matches = []
        for row in rows:
            same_tax = bool(target_tax and target_tax == _normalize_tax_id(row["supplier_tax_id"]))
            same_name = bool(
                not target_tax and target_name
                and target_name == normalize_supplier_name(row["supplier_name"])
            )
            if same_tax or same_name:
                matches.append(row)
        values = {row["value"] for row in matches}
        if len(values) != 1:
            return None
        return matches[0]
