"""Data model del dominio AP: proveedor, OC, factura, resultados y lotes.

Dinero siempre en Decimal; en JSON viaja como string para no perder precision.
Fechas como date; en JSON viajan ISO (YYYY-MM-DD).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


# ---------- Maestros ----------

@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    name: str
    tax_id: str
    iban: str                 # cuenta destino segun el maestro de proveedores
    bank_name: str
    payment_terms_days: int
    intercompany: bool
    category: str             # descripcion del servicio que presta


@dataclass(frozen=True)
class POLine:
    line_id: str
    description: str
    amount: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    po_id: str
    vendor_id: str
    project_code: str
    gl_account: str
    mgmt_category: str
    currency: str
    status: str               # "aprobada" | "borrador" | "cerrada"
    valid_from: date
    valid_to: date
    amount_authorized: Decimal
    lines: tuple[POLine, ...]

    def line(self, line_id: str) -> POLine | None:
        for ln in self.lines:
            if ln.line_id == line_id:
                return ln
        return None


@dataclass(frozen=True)
class Invoice:
    invoice_id: str           # id interno del sistema (INV-xxx)
    vendor_id: str
    vendor_name: str          # nombre tal como figura en la factura
    invoice_number: str       # numero del proveedor
    issue_date: date
    received_date: date
    currency: str
    amount_total: Decimal     # importe total, impuestos incluidos
    description: str
    po_ref: str | None        # OC referenciada (None si el email vino sin OC)
    po_line_ref: str | None
    iban_on_invoice: str      # cuenta que la factura pide pagar
    has_invoice_pdf: bool     # adjuntos del email simulado
    has_po_pdf: bool
    project_code: str | None  # codigo de proyecto que trae la factura


# ---------- Resultados del motor ----------

# Estados de pipeline (Dia 1). Los estados de gate humano (lote aprobado,
# liberado al banco) se agregan con el motor completo: "liberada_al_banco"
# es inalcanzable sin aprobacion humana registrada, por diseno.
STATUS_BLOQUEADA = "bloqueada"
STATUS_EN_LOTE = "en_lote"
STATUS_PROXIMO_CICLO = "proximo_ciclo"

SEVERITY_HARD = "hard"
SEVERITY_SOFT = "soft"


@dataclass(frozen=True)
class ControlResult:
    control_id: str
    control_name: str
    severity: str             # hard | soft
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    checker: str = ""         # agente checker que emitio el resultado


@dataclass
class InvoiceOutcome:
    invoice_id: str
    status: str               # bloqueada | en_lote | proximo_ciclo
    blocking_control: str | None
    flags: list[str]
    batch_date: date | None
    control_results: list[ControlResult]


@dataclass
class ExceptionItem:
    invoice_id: str
    control_id: str
    severity: str
    owner: str
    detail: str
    evidence: dict[str, Any]
    fraud_alert: bool = False


@dataclass
class PaymentBatch:
    batch_date: date
    invoice_ids: list[str]
    total: Decimal

    @property
    def count(self) -> int:
        return len(self.invoice_ids)


@dataclass
class RunResult:
    run_id: str
    commit: str
    outcomes: dict[str, InvoiceOutcome]
    batches: list[PaymentBatch]
    exceptions: list[ExceptionItem]
    carryover_ids: list[str]


# ---------- (De)serializacion JSON ----------

def _dec(v: str) -> Decimal:
    return Decimal(v)


def vendor_from_dict(d: dict) -> Vendor:
    return Vendor(**d)


def po_from_dict(d: dict) -> PurchaseOrder:
    return PurchaseOrder(
        po_id=d["po_id"],
        vendor_id=d["vendor_id"],
        project_code=d["project_code"],
        gl_account=d["gl_account"],
        mgmt_category=d["mgmt_category"],
        currency=d["currency"],
        status=d["status"],
        valid_from=date.fromisoformat(d["valid_from"]),
        valid_to=date.fromisoformat(d["valid_to"]),
        amount_authorized=_dec(d["amount_authorized"]),
        lines=tuple(
            POLine(line_id=l["line_id"], description=l["description"], amount=_dec(l["amount"]))
            for l in d["lines"]
        ),
    )


def invoice_from_dict(d: dict) -> Invoice:
    return Invoice(
        invoice_id=d["invoice_id"],
        vendor_id=d["vendor_id"],
        vendor_name=d["vendor_name"],
        invoice_number=d["invoice_number"],
        issue_date=date.fromisoformat(d["issue_date"]),
        received_date=date.fromisoformat(d["received_date"]),
        currency=d["currency"],
        amount_total=_dec(d["amount_total"]),
        description=d["description"],
        po_ref=d["po_ref"],
        po_line_ref=d["po_line_ref"],
        iban_on_invoice=d["iban_on_invoice"],
        has_invoice_pdf=d["has_invoice_pdf"],
        has_po_pdf=d["has_po_pdf"],
        project_code=d["project_code"],
    )


@dataclass(frozen=True)
class Dataset:
    vendors: dict[str, Vendor]
    pos: dict[str, PurchaseOrder]
    invoices: list[Invoice]     # ordenadas por recepcion al cargar


def dataset_from_dict(d: dict) -> Dataset:
    vendors = {v["vendor_id"]: vendor_from_dict(v) for v in d["vendors"]}
    pos = {p["po_id"]: po_from_dict(p) for p in d["purchase_orders"]}
    invoices = sorted(
        (invoice_from_dict(i) for i in d["invoices"]),
        key=lambda i: (i.received_date, i.invoice_id),
    )
    return Dataset(vendors=vendors, pos=pos, invoices=invoices)


def load_dataset(path: str) -> Dataset:
    import json

    with open(path, encoding="utf-8") as f:
        return dataset_from_dict(json.load(f))
