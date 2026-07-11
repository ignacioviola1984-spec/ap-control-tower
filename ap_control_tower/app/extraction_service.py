"""Caso de uso de extraccion documental (Fase 3).

Aisla a la UI del adaptador concreto de Document AI: la vista de documentos
reales llama a estas funciones, no a extraction/document_ai directamente. El
punto de sustitucion por otros extractores (Fase 8, adaptadores) es aca.
"""

from __future__ import annotations


def process_uploaded_document(filename: str, data: bytes):
    """Clasifica y estructura un PDF subido (Document AI o degradacion local)."""
    from ..extraction.document_ai import extract_uploaded_document
    return extract_uploaded_document(filename, data)


def document_ai_configured() -> bool:
    """True si Document AI esta configurado por entorno."""
    from ..extraction.document_ai import is_document_ai_configured
    return is_document_ai_configured()
