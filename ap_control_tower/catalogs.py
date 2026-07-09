"""Catalogos maestros sinteticos: plan de cuentas, BUs, proyectos, categorias.

El checker de imputacion (C4) valida contra estos catalogos.
Las 2 primeras letras del codigo de proyecto definen la unidad de negocio.
"""

from __future__ import annotations

# Plan de cuentas (sintetico, sabor PGC espanol, solo gasto AP)
CHART_OF_ACCOUNTS = {
    "621000": "Arrendamientos y canones",
    "622000": "Reparaciones y conservacion",
    "623000": "Servicios de profesionales independientes",
    "623100": "Subcontratacion de consultores",
    "623900": "Servicios intercompany (management fee / shared services)",
    "624000": "Transportes y viajes",
    "625000": "Primas de seguros",
    "627000": "Publicidad y marketing",
    "628000": "Suministros (telecom, cloud, licencias)",
    "629000": "Otros servicios",
    "629100": "Formacion",
}

# Unidades de negocio: prefijo de 2 letras del codigo de proyecto
BUSINESS_UNITS = {
    "CN": "Consultoria de Negocio",
    "TD": "Transformacion Digital",
    "FS": "Servicios Financieros",
    "CO": "Corporativo",
}

# Codigos de proyecto validos (catalogo vivo en el ERP simulado)
PROJECT_CODES = {
    "CO-001": "Estructura y oficina",
    "CO-002": "Intercompany UK",
    "CO-003": "Intercompany Mexico",
    "CO-005": "Asesoria legal recurrente",
    "CO-014": "IT interna y licencias",
    "CO-016": "Viajes corporativos",
    "CO-020": "Marketing y propuestas",
    "CO-030": "Talento y formacion",
    "TD-410": "Programa transformacion cliente industrial",
    "CN-215": "Proyecto operaciones retail",
    "FS-120": "Asesoria M&A cliente financiero",
}

# Categorias de imputacion de gestion
MGMT_CATEGORIES = {
    "Coste directo de proyecto",
    "Overhead general",
    "IT y sistemas",
    "Marketing y ventas",
    "Personas y talento",
    "Instalaciones y oficina",
    "Viajes",
}


def bu_from_project(project_code: str) -> str | None:
    """Deriva la BU del prefijo del codigo de proyecto; None si no mapea."""
    prefix = (project_code or "")[:2].upper()
    return prefix if prefix in BUSINESS_UNITS else None
