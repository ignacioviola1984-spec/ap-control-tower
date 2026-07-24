"""Procesamiento de PDF sin dependencias de presentación.

Vivía dentro de ``ui/components/extraction_view.py``, que es una vista heredada
y arrastra el sistema visual anterior (``ui/theme.py``) y APIs de Streamlit ya
deprecadas. El ingreso de documentos sólo necesita procesar; al importar la
vista entera cargaba todo lo demás dentro de la superficie activa.

Sin cache y sin estado: los bytes se procesan y se descartan. Quien llama
decide qué conserva y dónde.
"""

from __future__ import annotations

from time import perf_counter


def process_files(files, on_progress=None):
    """Procesa PDFs INLINE (sin cache): [(nombre, bytes)] -> (results, errores).

    Reutiliza el único adaptador de Document AI. No guarda los bytes: se
    procesan y se descartan; sólo se devuelve el resultado estructurado.
    ``on_progress(i, total, nombre)`` es opcional para feedback de interfaz.
    """
    from ..app import process_uploaded_document

    results, errors = [], []
    total = len(files)
    for index, (name, data) in enumerate(files, 1):
        try:
            results.append(process_uploaded_document(name, data))
        except Exception as exc:  # protección de red/API: mensaje claro, no crash
            errors.append((name, str(exc)))
        if on_progress is not None:
            on_progress(index, total, name)
    return results, errors


def process_one(name, data):
    """Procesa UN PDF inline y mide su tiempo. -> (result|None, error|None, segundos)."""
    from ..app import process_uploaded_document

    started = perf_counter()
    try:
        result = process_uploaded_document(name, data)
        return result, None, perf_counter() - started
    except Exception as exc:  # red/API: mensaje claro, no crash
        return None, str(exc), perf_counter() - started


__all__ = ["process_files", "process_one"]
