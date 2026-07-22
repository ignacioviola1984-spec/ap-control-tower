"""Conector Zoho reutilizable para CRM y WorkDrive.

Las credenciales se leen exclusivamente desde variables de entorno inyectadas
por Secret Manager. Importar este paquete no realiza llamadas de red.
"""

from .client import (
    CRM_SCOPES,
    WORKDRIVE_SCOPES,
    ZohoConfig,
    ZohoConnector,
    ZohoConnectorError,
    build_connector,
    zoho_configured,
)

__all__ = [
    "CRM_SCOPES",
    "WORKDRIVE_SCOPES",
    "ZohoConfig",
    "ZohoConnector",
    "ZohoConnectorError",
    "build_connector",
    "zoho_configured",
]
