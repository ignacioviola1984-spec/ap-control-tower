"""Fixtures locales para capturas; solo se activan con ``AP_PREVIEW_MODE=1``."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib

import streamlit as st

from ..extraction.pdf_poc import PocResult
from ..extraction.synthetic_fixtures import FIXTURES
from .trial import session as sess


def seed_preview_session() -> None:
    if st.session_state.get("_pilot_preview_seeded"):
        return
    active = sess.get_session()
    if active.results or active.errors:
        st.session_state["_pilot_preview_seeded"] = True
        return

    warning_by_id = {
        "EXT-003": ["Revisar la identificación fiscal del proveedor."],
    }
    for index, (doc_id, document) in enumerate(FIXTURES.items(), 1):
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
