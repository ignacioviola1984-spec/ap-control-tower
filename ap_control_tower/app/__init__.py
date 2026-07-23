"""Capa de aplicacion de AP Control Tower (casos de uso).

Punto de entrada unico para la UI (y una futura API): la interfaz llama a estas
funciones y NO importa engine/ ni extraction/ directamente, ni contiene reglas
centrales de negocio. Framework-agnostico (sin Streamlit). Ver services.py.
"""

from __future__ import annotations

# Errores del dominio que la UI captura para UX (re-exportados: la vista no
# importa engine/ para atraparlos).
from ..engine.batch import (
    ESTADO_APROBADO,
    ESTADO_DETENIDO,
    ESTADO_LIBERADO,
    ESTADO_PENDIENTE_HUMANO,
    ESTADO_RECHAZADO,
    GateViolation,
)
from ..engine.review import ReviewError
# Utilidades de solo-lectura/derivacion que la UI usa para MOSTRAR (no son
# reglas de negocio): clasificacion de documento y orden de campos del esquema.
from ..engine.controls import classify_document
from ..extraction.schema import FIELD_ORDER
from .extraction_service import document_ai_configured, process_uploaded_document
from .master_data_service import (
    SageMasterError,
    SageVendorMaster,
    SupplierResolution,
    match_supplier_to_sage,
    parse_sage_vendor_master,
)
from .services import (
    approve_and_release,
    approve_anticipo,
    assignable_thursdays,
    build_run,
    close_batch,
    confirm_internal_data,
    finalize_runner,
    new_month_runner,
    process_month,
    reject_batch,
    reopen_workflow,
)

__all__ = [
    # errores
    "GateViolation", "ReviewError",
    # constantes de estado de lote (solo para render/UX)
    "ESTADO_APROBADO", "ESTADO_DETENIDO", "ESTADO_LIBERADO",
    "ESTADO_PENDIENTE_HUMANO", "ESTADO_RECHAZADO",
    # corrida
    "process_month", "build_run", "finalize_runner", "new_month_runner",
    # lotes / gate
    "assignable_thursdays", "reopen_workflow",
    "approve_and_release", "reject_batch", "close_batch",
    # revision humana
    "confirm_internal_data", "approve_anticipo",
    # extraccion
    "process_uploaded_document", "document_ai_configured",
    # maestro Sage
    "SageMasterError", "SageVendorMaster", "SupplierResolution",
    "parse_sage_vendor_master", "match_supplier_to_sage",
    # utilidades de display (no reglas de negocio)
    "classify_document", "FIELD_ORDER",
]
