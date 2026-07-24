"""Fixtures locales para capturas; solo se activan con ``AP_PREVIEW_MODE=1``.

Los documentos sintéticos reproducen los riesgos de pago REALES detectados en
el semestre de Brand UP (factura ya saldada, anticipo ya facturado, duplicado,
destinatario ajeno al grupo). Sirven para ver el circuito completo sin usar
facturas de cliente.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib

import streamlit as st

from ..extraction.pdf_poc import PocResult
from ..extraction.schema import empty_document
from ..extraction.synthetic_fixtures import FIXTURES
from .trial import session as sess


def _dia(offset: int) -> str:
    """Fecha relativa a hoy: la vista previa tiene que seguir siendo vigente."""
    return (date.today() + timedelta(days=offset)).isoformat()


def _factura(**campos) -> dict:
    document = empty_document()
    document.update({
        "document_type": "invoice",
        "moneda": "EUR",
        "tratamiento_iva": "nacional",
        "metodo_pago": "transferencia",
        "cliente_nombre": "BRAND UP SLU",
        "cliente_tax_id": "B85902583",
    })
    document.update(campos)
    return document


#: Casos que ejercitan los controles de pago del circuito.
RIESGOS: dict[str, dict] = {
    # Vence en 3 días y es el importe más alto del lote: encabeza el briefing.
    "EXT-101": _factura(
        proveedor_nombre_comercial="Meridian Fieldwork SL",
        proveedor_razon_social_legal="Meridian Fieldwork SL",
        proveedor_tax_id="B87654321", numero_factura="2026-4417",
        fecha_emision=_dia(-24), fecha_vencimiento_calculada=_dia(3),
        importe_neto="18400.00", tipo_iva="21", importe_iva="3864.00",
        importe_total="22264.00", iban="ES9121000418450200051332",
    ),
    # Saldada por tarjeta: si entra al lote se paga dos veces.
    "EXT-102": _factura(
        proveedor_nombre_comercial="Cloudmeet Communications",
        proveedor_tax_id="IE6388047V", numero_factura="INV-556120",
        fecha_emision=_dia(-12), fecha_vencimiento_calculada=_dia(5),
        importe_neto="105.98", tipo_iva="0", importe_iva="0.00",
        importe_total="105.98", saldo_pendiente="0.00",
        metodo_pago="tarjeta", tratamiento_iva="intracomunitario_inversion_sujeto_pasivo",
    ),
    # Anticipo ya facturado aparte: pagar el total sobrepaga 2.040 EUR.
    "EXT-103": _factura(
        proveedor_nombre_comercial="Gestión y Marketing SL",
        proveedor_tax_id="B81380495", numero_factura="144/2026",
        fecha_emision=_dia(-6), fecha_vencimiento_calculada=_dia(24),
        importe_neto="3795.00", tipo_iva="21", importe_iva="796.95",
        importe_total="4591.95", saldo_pendiente="2551.95",
        iban="ES5400730100520424692163",
    ),
    # Emitida a un tercero: no es deuda del grupo.
    "EXT-104": _factura(
        proveedor_nombre_comercial="Leftfield Research Ltd",
        proveedor_tax_id="GB691014453", numero_factura="11058",
        fecha_emision=_dia(-9), fecha_vencimiento_calculada=_dia(21),
        importe_neto="7300.00", tipo_iva="0", importe_iva="0.00",
        importe_total="7300.00", moneda="GBP",
        cliente_nombre="Research & Thinking LLC", cliente_tax_id="US067014987",
        tratamiento_iva="extracomunitario",
    ),
    # Par duplicado: mismo proveedor, número e importe.
    "EXT-105": _factura(
        proveedor_nombre_comercial="Papelería Central SA",
        proveedor_tax_id="A28001122", numero_factura="F-2026-0912",
        fecha_emision=_dia(-15), fecha_vencimiento_calculada=_dia(-2),
        importe_neto="980.00", tipo_iva="21", importe_iva="205.80",
        importe_total="1185.80",
    ),
    "EXT-106": _factura(
        proveedor_nombre_comercial="Papelería Central SA",
        proveedor_tax_id="A28001122", numero_factura="F-2026-0912",
        fecha_emision=_dia(-15), fecha_vencimiento_calculada=_dia(-2),
        importe_neto="980.00", tipo_iva="21", importe_iva="205.80",
        importe_total="1185.80",
    ),
}

#: Advertencias del extractor que acompañan a cada caso.
AVISOS: dict[str, list[str]] = {
    "EXT-003": ["Revisar la identificación fiscal del proveedor."],
    "EXT-102": ["saldo pendiente 0 informado por el emisor"],
    "EXT-103": ["el documento descuenta un anticipo ya facturado"],
    "EXT-104": ["baja confianza en: cliente_nombre"],
}


def seed_preview_session() -> None:
    if st.session_state.get("_pilot_preview_seeded"):
        return
    active = sess.get_session()
    if active.results or active.errors:
        st.session_state["_pilot_preview_seeded"] = True
        return

    warning_by_id = AVISOS
    for index, (doc_id, document) in enumerate(
            {**FIXTURES, **RIESGOS}.items(), 1):
        result = PocResult(
            doc_id=doc_id,
            archivo=f"{doc_id.lower()}.pdf",
            pages=1,
            text_chars=680 + index * 47,
            confidence=Decimal("0.88") - Decimal(index) / Decimal("100"),
            warnings=list(warning_by_id.get(doc_id, [])),
            document=deepcopy(document),
            engine="fallback_local",
            field_confidences={
                "numero_factura": Decimal("0.91"),
                "importe_total": Decimal("0.94"),
                "moneda": Decimal("0.96"),
            },
        )
        sess.add_document(
            active,
            result,
            seconds=0.45 + index / 10,
            file_hash=hashlib.sha256(doc_id.encode("utf-8")).hexdigest(),
            source="carga-manual",
        )
    sess.add_error(
        active,
        "documento-incompleto.pdf",
        "El PDF no pudo leerse. Verificá que el archivo no esté dañado o protegido.",
        seconds=0.2,
    )
    sess.retain_review(
        active,
        "EXT-001",
        "Laura Gómez",
        "Falta la factura fiscal definitiva antes de continuar.",
    )
    sess.decide_payment_proposal(
        active,
        ["EXT-005"],
        "Martín López",
        "approved",
        "Incluido en la propuesta semanal.",
    )
    st.session_state["_pilot_preview_seeded"] = True
