"""Casos de uso de maestros externos, sin dependencia de Streamlit."""

from __future__ import annotations

from ..sage import (
    SageMasterError,
    SageVendorMaster,
    SupplierResolution,
    load_vendor_master_xlsx,
    resolve_document_supplier,
)


def parse_sage_vendor_master(content: bytes, filename: str) -> SageVendorMaster:
    return load_vendor_master_xlsx(content, filename=filename)


def match_supplier_to_sage(
    document: dict, master: SageVendorMaster
) -> SupplierResolution:
    return resolve_document_supplier(document, master)


__all__ = [
    "SageMasterError",
    "SageVendorMaster",
    "SupplierResolution",
    "parse_sage_vendor_master",
    "match_supplier_to_sage",
]
