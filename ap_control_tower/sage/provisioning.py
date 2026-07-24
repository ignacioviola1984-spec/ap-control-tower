"""Maestro de proveedores aprovisionado por el sistema, no por el usuario.

El maestro es un dato de la instalación: se configura una vez y se aplica solo.
Pedirle al operador que lo suba en cada sesión hacía que un olvido dejara al
circuito sin nada contra qué reconciliar.

Ubicación por orden de precedencia:
  1. ``AP_VENDOR_MASTER_PATH`` (ruta explícita, la que se usa en Cloud Run)
  2. ``data/sage/vendor_master.xlsx`` dentro del paquete desplegado
"""

from __future__ import annotations

import os
from pathlib import Path

from .vendor_master import SageMasterError, SageVendorMaster, load_vendor_master_xlsx

ENV_VAR = "AP_VENDOR_MASTER_PATH"
#: Ruta por defecto, relativa a la raíz del repo/imagen.
DEFAULT_RELATIVE_PATH = Path("data") / "sage" / "vendor_master.xlsx"


def _repo_root() -> Path:
    # ap_control_tower/sage/provisioning.py -> raíz del proyecto
    return Path(__file__).resolve().parent.parent.parent


def vendor_master_path() -> Path | None:
    """Ruta del maestro aprovisionado, o None si no hay ninguno instalado."""
    configured = os.environ.get(ENV_VAR, "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None
    candidate = _repo_root() / DEFAULT_RELATIVE_PATH
    return candidate if candidate.is_file() else None


def load_provisioned_vendor_master() -> SageVendorMaster | None:
    """Carga el maestro instalado. None si no hay archivo o no es legible.

    Nunca propaga la excepción: un maestro mal instalado no puede impedir que
    el circuito de facturas arranque; la falta de conciliación ya se informa
    como advertencia en el propio documento.
    """
    path = vendor_master_path()
    if path is None:
        return None
    try:
        return load_vendor_master_xlsx(path.read_bytes(), filename=path.name)
    except (SageMasterError, OSError, ValueError):
        return None
