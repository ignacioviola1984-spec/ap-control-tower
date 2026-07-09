"""Controles del pipeline maker-checker.

Cada control es una funcion pura que recibe la factura + el contexto de la
corrida y devuelve un ControlResult con evidencia (esperado vs recibido).
Los checkers son independientes del maker que produjo el dato: validan
contra reglas explicitas y catalogos, nunca contra la salida del maker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..catalogs import BUSINESS_UNITS, CHART_OF_ACCOUNTS, MGMT_CATEGORIES, PROJECT_CODES, bu_from_project
from ..config import CONTROL_NAMES, Controls, EngineConfig
from ..models import (
    ControlResult,
    Dataset,
    Invoice,
    PurchaseOrder,
    SEVERITY_HARD,
    SEVERITY_SOFT,
)


@dataclass
class RunContext:
    """Estado acumulado de la corrida que los controles necesitan consultar."""
    dataset: Dataset
    config: EngineConfig
    ingested: list[Invoice] = field(default_factory=list)          # historia para duplicados
    po_consumed: dict[str, Decimal] = field(default_factory=dict)  # consumo por OC (solo facturas limpias)
    cashflow: dict[str, dict] = field(default_factory=dict)        # registro operativo simulado
    erp: dict[str, dict] = field(default_factory=dict)             # registro contable simulado


def _result(control_id: str, severity: str, passed: bool, detail: str,
            evidence: dict[str, Any], checker: str) -> ControlResult:
    return ControlResult(
        control_id=control_id,
        control_name=CONTROL_NAMES[control_id],
        severity=severity,
        passed=passed,
        detail=detail,
        evidence=evidence,
        checker=checker,
    )


# ------------------------------------------------------------------ C1
def check_completitud(inv: Invoice, ctx: RunContext) -> ControlResult:
    """Hard: el email debe traer factura y OC (ambos PDFs) y referencia de OC."""
    missing = []
    if not inv.has_invoice_pdf:
        missing.append("factura (PDF)")
    if not inv.has_po_pdf:
        missing.append("orden de compra (PDF)")
    if inv.po_ref is None:
        missing.append("referencia de OC")
    passed = not missing
    return _result(
        Controls.C1_COMPLETITUD, SEVERITY_HARD, passed,
        "Documentacion completa" if passed else f"Faltan adjuntos: {', '.join(missing)}",
        {"esperado": "factura (PDF) + orden de compra (PDF)",
         "recibido": "factura (PDF)" + (" + orden de compra (PDF)" if inv.has_po_pdf else " solamente"),
         "faltante": missing},
        checker="checker-recepcion",
    )


# ------------------------------------------------------------------ C2
def check_duplicados(inv: Invoice, ctx: RunContext) -> ControlResult:
    """Hard: duplicado exacto (proveedor+numero+importe+fecha) o casi-duplicado
    (mismo proveedor e importe, numero distinto, emision a <= N dias)."""
    window = ctx.config.near_dup_window_days
    for prev in ctx.ingested:
        if prev.vendor_id != inv.vendor_id:
            continue
        exact = (
            prev.invoice_number == inv.invoice_number
            and prev.amount_total == inv.amount_total
            and prev.issue_date == inv.issue_date
        )
        near = (
            not exact
            and prev.invoice_number != inv.invoice_number
            and prev.amount_total == inv.amount_total
            and abs((prev.issue_date - inv.issue_date).days) <= window
        )
        if exact or near:
            kind = "duplicado exacto" if exact else "casi-duplicado"
            return _result(
                Controls.C2_DUPLICADOS, SEVERITY_HARD, False,
                f"{kind.capitalize()} de {prev.invoice_id} ({prev.invoice_number})",
                {"tipo": kind,
                 "factura_original": prev.invoice_id,
                 "numero_original": prev.invoice_number,
                 "numero_recibido": inv.invoice_number,
                 "importe": str(inv.amount_total),
                 "emision_original": prev.issue_date.isoformat(),
                 "emision_recibida": inv.issue_date.isoformat(),
                 "ventana_dias": window},
                checker="checker-duplicados",
            )
    return _result(
        Controls.C2_DUPLICADOS, SEVERITY_HARD, True,
        "Sin duplicados en la historia del mes",
        {"comparadas_contra": len(ctx.ingested)},
        checker="checker-duplicados",
    )


# ------------------------------------------------------------------ C3
def check_autorizacion_oc(inv: Invoice, ctx: RunContext) -> ControlResult:
    """Hard: OC existe, aprobada, vigente a la fecha de recepcion, con saldo."""
    po = ctx.dataset.pos.get(inv.po_ref or "")
    if po is None:
        return _result(
            Controls.C3_AUTORIZACION_OC, SEVERITY_HARD, False,
            f"OC referenciada inexistente: {inv.po_ref}",
            {"esperado": "OC valida en el sistema", "recibido": str(inv.po_ref)},
            checker="checker-autorizacion",
        )
    problems: list[str] = []
    if po.status != "aprobada":
        problems.append(f"estado '{po.status}' (se requiere 'aprobada')")
    if not (po.valid_from <= inv.received_date <= po.valid_to):
        problems.append("OC fuera de vigencia a la fecha de recepcion")
    consumed = ctx.po_consumed.get(po.po_id, Decimal("0"))
    available = po.amount_authorized - consumed
    if inv.amount_total > available:
        problems.append(
            f"saldo insuficiente: autorizado {po.amount_authorized}, "
            f"consumido {consumed}, disponible {available}, factura {inv.amount_total}"
        )
    passed = not problems
    return _result(
        Controls.C3_AUTORIZACION_OC, SEVERITY_HARD, passed,
        "OC aprobada, vigente y con saldo" if passed else "; ".join(problems),
        {"po_id": po.po_id,
         "estado_oc": po.status,
         "vigencia": f"{po.valid_from.isoformat()} a {po.valid_to.isoformat()}",
         "autorizado": str(po.amount_authorized),
         "consumido_previo": str(consumed),
         "disponible": str(available),
         "importe_factura": str(inv.amount_total)},
        checker="checker-autorizacion",
    )


# ------------------------------------------------------------------ C4
def maker_propose_imputacion(inv: Invoice, po: PurchaseOrder) -> dict[str, Any]:
    """MAKER: propone imputacion contable y de gestion leyendo la OC
    (pasos 5-7 del proceso: la imputacion viene definida en la OC)."""
    vendor_ic = None  # se resuelve en el checker con el maestro
    return {
        "gl_account": po.gl_account,
        "mgmt_category": po.mgmt_category,
        "project_code": po.project_code,
        "bu": bu_from_project(po.project_code),
        "clasificacion_ic": vendor_ic,
        "fuente": f"OC {po.po_id}",
    }


def checker_validate_imputacion(inv: Invoice, proposal: dict[str, Any], ctx: RunContext) -> ControlResult:
    """CHECKER independiente: valida la propuesta contra plan de cuentas y
    catalogos, y clasifica local vs intercompany con el maestro de proveedores.
    Soft: una propuesta invalida genera flag, no bloquea."""
    issues: list[str] = []
    if proposal["gl_account"] not in CHART_OF_ACCOUNTS:
        issues.append(f"cuenta {proposal['gl_account']} fuera del plan de cuentas")
    if proposal["project_code"] not in PROJECT_CODES:
        issues.append(f"proyecto {proposal['project_code']} fuera de catalogo")
    if proposal["bu"] not in BUSINESS_UNITS:
        issues.append(f"BU no derivable del proyecto {proposal['project_code']}")
    if proposal["mgmt_category"] not in MGMT_CATEGORIES:
        issues.append(f"categoria de gestion '{proposal['mgmt_category']}' fuera de catalogo")

    vendor = ctx.dataset.vendors[inv.vendor_id]
    clasificacion = "intercompany" if vendor.intercompany else "local"
    passed = not issues
    return _result(
        Controls.C4_IMPUTACION, SEVERITY_SOFT, passed,
        (f"Imputacion validada; clasificacion {clasificacion}"
         if passed else "Propuesta con observaciones: " + "; ".join(issues)),
        {"propuesta_maker": proposal,
         "clasificacion": clasificacion,
         "observaciones": issues},
        checker="checker-imputacion",
    )


# ------------------------------------------------------------------ C5
def check_match(inv: Invoice, ctx: RunContext) -> ControlResult:
    """Match factura vs OC en 4 dimensiones con tolerancias explicitas:
    proveedor (hard), moneda (hard), importe (% y absoluto: hard si supera
    materialidad, soft si es menor), BU (soft)."""
    cfg = ctx.config
    po = ctx.dataset.pos[inv.po_ref]  # C3 ya garantizo que existe
    line = po.line(inv.po_line_ref or "") if inv.po_line_ref else None
    expected_amount = line.amount if line else po.amount_authorized

    hard_issues: list[str] = []
    soft_issues: list[str] = []
    if po.vendor_id != inv.vendor_id:
        hard_issues.append(f"proveedor de la OC ({po.vendor_id}) distinto del de la factura ({inv.vendor_id})")
    if po.currency != inv.currency:
        hard_issues.append(f"moneda OC {po.currency} vs factura {inv.currency}")

    diff = inv.amount_total - expected_amount
    diff_pct = (abs(diff) / expected_amount * 100).quantize(Decimal("0.01")) if expected_amount else Decimal("0")
    if diff != 0:
        desc = (f"importe factura {inv.amount_total} vs OC {expected_amount} "
                f"(dif {diff:+} EUR, {diff_pct}%)")
        if abs(diff) > cfg.match_materiality_abs or diff_pct > cfg.match_materiality_pct:
            hard_issues.append(desc + " supera materialidad")
        else:
            soft_issues.append(desc + " bajo materialidad")

    inv_bu = bu_from_project(inv.project_code or "")
    po_bu = bu_from_project(po.project_code)
    if inv.project_code and inv_bu != po_bu:
        soft_issues.append(f"BU de factura ({inv_bu}) distinta de la OC ({po_bu})")

    evidence = {
        "po_id": po.po_id,
        "linea_oc": inv.po_line_ref,
        "importe_esperado_oc": str(expected_amount),
        "importe_factura": str(inv.amount_total),
        "diferencia": str(diff),
        "diferencia_pct": str(diff_pct),
        "materialidad_pct": str(cfg.match_materiality_pct),
        "materialidad_abs": str(cfg.match_materiality_abs),
        "hard": hard_issues,
        "soft": soft_issues,
    }
    if hard_issues:
        return _result(Controls.C5_MATCH, SEVERITY_HARD, False,
                       "Match fuera de tolerancia: " + "; ".join(hard_issues),
                       evidence, checker="checker-match")
    if soft_issues:
        return _result(Controls.C5_MATCH, SEVERITY_SOFT, False,
                       "Diferencia menor bajo materialidad: " + "; ".join(soft_issues),
                       evidence, checker="checker-match")
    return _result(Controls.C5_MATCH, SEVERITY_HARD, True,
                   "Match exacto factura vs OC", evidence, checker="checker-match")


# ------------------------------------------------------------------ C6
def check_datos_bancarios(inv: Invoice, ctx: RunContext) -> ControlResult:
    """Hard + alerta de fraude: la cuenta destino de la factura debe ser
    identica a la del maestro de proveedores. Cualquier diferencia bloquea."""
    vendor = ctx.dataset.vendors[inv.vendor_id]
    passed = inv.iban_on_invoice == vendor.iban
    return _result(
        Controls.C6_DATOS_BANCARIOS, SEVERITY_HARD, passed,
        ("Cuenta destino coincide con el maestro" if passed else
         "ALERTA DE POSIBLE FRAUDE: la cuenta destino de la factura NO es la del maestro de proveedores"),
        {"iban_maestro": vendor.iban,
         "iban_factura": inv.iban_on_invoice,
         "banco_maestro": vendor.bank_name,
         "accion_recomendada": (None if passed else
                                "NO pagar. Verificar con el proveedor por canal independiente "
                                "(telefono conocido, nunca respondiendo al email recibido).")},
        checker="checker-tesoreria",
    )


# ------------------------------------------------------------------ C7
def check_conciliacion(inv: Invoice, ctx: RunContext) -> ControlResult:
    """Hard: antes de proponer pago, el registro operativo (cashflow) y el
    contable (ERP) deben coincidir: existe en ambos, mismo importe,
    contabilizada, matcheada y sin disputa."""
    cf = ctx.cashflow.get(inv.invoice_id)
    erp = ctx.erp.get(inv.invoice_id)
    problems: list[str] = []
    if cf is None:
        problems.append("no figura en el registro operativo (cashflow)")
    if erp is None:
        problems.append("no figura en el registro contable (ERP)")
    if cf and erp:
        if cf["amount"] != erp["amount"]:
            problems.append(f"importes divergentes: cashflow {cf['amount']} vs ERP {erp['amount']}")
        if erp.get("status") != "contabilizada":
            problems.append(f"estado contable '{erp.get('status')}' (se requiere 'contabilizada')")
        if not erp.get("matched"):
            problems.append("sin match confirmado contra OC")
        if cf.get("disputa"):
            problems.append("factura en disputa")
    passed = not problems
    return _result(
        Controls.C7_CONCILIACION, SEVERITY_HARD, passed,
        "Conciliada: operativo y contable coinciden" if passed else "; ".join(problems),
        {"cashflow": {k: str(v) for k, v in (cf or {}).items()},
         "erp": {k: str(v) for k, v in (erp or {}).items()}},
        checker="checker-conciliacion",
    )
