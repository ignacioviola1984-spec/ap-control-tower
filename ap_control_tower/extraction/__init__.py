"""Extraccion de documentos de proveedor (v2): esquema, prompt y comparador.

Modulo independiente del motor de la demo: define QUE se extrae de una
factura/proforma real (esquema v2, ajustado con el analisis de facturas
reales del cliente), COMO se le pide a un extractor (prompt con regla
anti-alucinacion) y COMO se evalua contra etiquetado humano (comparador
donde los null cuentan y las alucinaciones se reportan por separado).
"""
