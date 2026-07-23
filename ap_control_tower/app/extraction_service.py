"""Caso de uso de extraccion documental (Fase 3).

Aisla a la UI del adaptador concreto de Document AI: la vista de documentos
reales llama a estas funciones, no a extraction/document_ai directamente. El
punto de sustitucion por otros extractores (Fase 8, adaptadores) es aca.
"""

from __future__ import annotations

import os
import sqlite3


def process_uploaded_document(filename: str, data: bytes):
    """Clasifica y estructura un PDF subido (Document AI o degradacion local)."""
    from ..extraction.document_ai import extract_uploaded_document
    result = extract_uploaded_document(filename, data)
    memory_path = os.getenv("AP_EVIDENCE_MEMORY_PATH", "").strip()
    if memory_path:
        try:
            from ..evidence_memory import HistoricalEvidenceMemory

            HistoricalEvidenceMemory(memory_path).enrich_result(result, data)
        except (FileNotFoundError, OSError, sqlite3.DatabaseError) as exc:
            result.warnings.append(
                "memoria histórica no disponible; se continuó sin enriquecimiento "
                f"({type(exc).__name__})"
            )
    return result


def document_ai_configured() -> bool:
    """True si Document AI esta configurado por entorno."""
    from ..extraction.document_ai import is_document_ai_configured
    return is_document_ai_configured()
