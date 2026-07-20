"""Controles ARCA (Argentina): padron de contribuyentes y base APOC.

C10_PADRON: el CUIT del proveedor existe, esta activo y su condicion fiscal
es coherente con el tipo de comprobante. C11_APOC: el proveedor no figura en
la base de facturas apocrifas de ARCA. Ambos son validaciones deterministas:
derivan a revision humana con motivo explicito, nunca por score.

Fuentes verificadas y runbook: docs_operacion/runbook_controles_arca.md.
"""
