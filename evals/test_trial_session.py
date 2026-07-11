"""Eval: estado session-only del trial. exit 0 = verde.

Hermetico (no arranca Streamlit, no toca red ni disco): usa un resultado de
extraccion de mentira (duck-typed) para ejercitar el modelo puro de la sesion y
los helpers de presentacion.

Valida: los resultados viven en la sesion; el audit trail es temporal y sin PII;
'Finalizar y borrar' limpia las claves de la sesion; una sesion nueva no ve
resultados anteriores (aislamiento).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


@dataclass
class FakeResult:
    doc_id: str
    document: dict
    engine: str = "fallback_local"
    confidence: Decimal = Decimal("0.42")
    pages: int = 1
    text_chars: int = 120
    warnings: list = field(default_factory=list)
    field_confidences: dict = field(default_factory=dict)


def _fake_result(doc_id: str, numero: str, total: str, warn: bool) -> FakeResult:
    from ap_control_tower.extraction.schema import empty_document
    doc = empty_document()
    doc["document_type"] = "invoice"
    doc["numero_factura"] = numero
    doc["proveedor_nombre_comercial"] = "Proveedor Test SL"
    doc["proveedor_tax_id"] = "ESB12345678"
    doc["importe_total"] = total
    doc["moneda"] = "EUR"
    return FakeResult(
        doc_id=doc_id, document=doc,
        warnings=(["campos criticos ausentes"] if warn else []),
        field_confidences={"numero_factura": Decimal("0.9")},
    )


def main() -> int:
    from ap_control_tower.ui.components import extraction_view as ev
    from ap_control_tower.ui.trial import session as se

    print("== Modelo de sesion: resultados + audit temporal ==")
    s = se.new_session()
    check([e.action for e in s.audit.events] == ["sesion-iniciada"],
          "la sesion arranca con un evento 'sesion-iniciada'")
    r1 = _fake_result("DOC-1", "F-001", "121.00", warn=False)
    r2 = _fake_result("DOC-2", "F-002", "242.00", warn=True)
    se.add_results(s, [r1, r2])
    se.record_intake(s, canal="carga-manual", cantidad=2)
    check(len(s.results) == 2, "dos resultados guardados en la sesion")
    check(s.audit.verify_chain(), "cadena de auditoria de la sesion integra")
    actions = [e.action for e in s.audit.events]
    check(actions == ["sesion-iniciada", "documento-procesado", "documento-procesado", "ingesta"],
          f"eventos esperados en orden ({actions})")

    print("== Privacidad: el audit trail no guarda contenido del documento ==")
    blob = " ".join(str(e.evidence) for e in s.audit.events)
    check("F-001" not in blob and "ESB12345678" not in blob and "121.00" not in blob,
          "ningun valor de campo/PII en la evidencia de auditoria")
    ev0 = s.audit.events[1].evidence
    check("tipo" in ev0 and "confianza" in ev0 and "motor" in ev0,
          "la evidencia guarda solo metadatos (tipo/confianza/motor)")

    print("== Helpers de presentacion (cobertura, ausentes, CSV) ==")
    check(0.0 < ev.coverage(r1) < 1.0, "cobertura entre 0 y 1")
    check(len(ev.present_fields(r1)) + len(ev.missing_fields(r1)) == len(ev.BUSINESS_FIELDS),
          "encontrados + ausentes == total de campos de negocio")
    csv = ev.results_csv([r1, r2])
    check(csv.splitlines()[0].startswith("archivo,motor,confidence"),
          "CSV con encabezado esperado")
    check(len(csv.splitlines()) == 3, "CSV con una fila por documento")

    print("== 'Finalizar y borrar' limpia la sesion ==")
    to_clear = se.session_keys_to_clear(
        ["_trial_session", "_trial_uploader", "_gmail_demo_results", "otra_clave"])
    check("_trial_session" in to_clear and "_trial_uploader" in to_clear,
          "borra las claves de la sesion trial")
    check("otra_clave" not in to_clear, "no toca claves ajenas a la sesion trial")

    print("== Aislamiento: una sesion nueva no ve resultados anteriores ==")
    s2 = se.new_session()
    check(len(s2.results) == 0 and s2 is not s,
          "sesion nueva vacia e independiente de la anterior")

    print()
    if failures:
        print(f"TRIAL SESSION ROJO: {len(failures)} fallas")
        return 1
    print("TRIAL SESSION VERDE: session-only, audit temporal sin PII, borrado y aislamiento (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
