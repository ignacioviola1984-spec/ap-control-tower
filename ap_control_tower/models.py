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
    tax_id: str | None        # None/vacio -> retencion hasta completar alta
    iban: str                 # cuenta destino segun el maestro de proveedores
    bank_name: str
    payment_terms_days: int
    intercompany: bool
    category: str             # descripcion del servicio que presta
    country: str = "ES"
    razon_social_confirmada: bool = True   # False -> razon social ambigua: retencion
    sepa_mandate_ref: str | None = None    # mandato SEPA registrado (domiciliacion)


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
    invoice_number: str | None  # numero fiscal del proveedor (None en proformas)
    issue_date: date
    received_date: date
    currency: str
    amount_total: Decimal     # importe total, impuestos incluidos
    description: str
    po_ref: str | None        # OC referenciada (None = documento non-PO)
    po_line_ref: str | None
    iban_on_invoice: str | None  # cuenta que el documento pide pagar (None en DD/tarjeta)
    has_invoice_pdf: bool     # adjuntos del email simulado
    has_po_pdf: bool
    project_code: str | None  # codigo de proyecto que trae la factura
    # Importe que YA estaba tipeado a mano en el Excel de cashflow antes de que
    # el agente procese el email (None = no habia carga manual previa). Simula
    # el registro operativo heredado; si difiere del importe real, C7 lo detecta.
    cashflow_amount_manual: Decimal | None = None

    # --- flujos reales (non-PO, proformas, metodos de pago) ---
    tratamiento_iva: str = "nacional"       # nacional | intracomunitario_inversion_sujeto_pasivo | no_desglosado
    metodo_pago: str = "transferencia"      # transferencia | domiciliacion_direct_debit | tarjeta
    menciona_factura_final: bool = False    # senial de proforma/anticipo
    presupuesto_aprobado: bool | None = None    # proformas: aprobacion interna del presupuesto
    anticipo_pagado: bool = False               # proformas: el anticipo ya se pago
    factura_final_ref: str | None = None        # proformas: factura fiscal final vinculada
    internal_approver: str | None = None    # non-PO: aprobador interno asignado
    cost_center: str | None = None          # non-PO: centro de coste asignado
    contract_ref: str | None = None         # non-PO: contrato o soporte referenciado


# ---------- Resultados del motor ----------

# Estados de documento. El pipeline emite los de la primera lista; los de
# gate/cierre solo pueden crearlos engine/batch.py y engine/closing.py.
# "liberada_al_banco" es inalcanzable sin aprobacion humana registrada,
# por diseno y por eval.
STATUS_BLOQUEADA = "bloqueada"
STATUS_EN_LOTE = "en_lote"
STATUS_PROXIMO_CICLO = "proximo_ciclo"
# Retenciones (pendientes de datos, NO bloqueos por control):
STATUS_PENDIENTE_DATOS_INTERNOS = "pendiente_datos_internos"    # non-PO sin gobierno completo
STATUS_RETENIDO_ALTA_PROVEEDOR = "retenido_alta_proveedor"      # vendor master incompleto
# Flujo de anticipos (proformas; JAMAS entran a un lote de pago):
STATUS_ANTICIPO_RETENIDO = "anticipo_retenido_sin_aprobacion"
STATUS_ANTICIPO_PENDIENTE = "anticipo_pendiente_factura_final"
STATUS_ANTICIPO_EXCEPCION = "anticipo_pagado_sin_factura_final"
# Metodos de pago que no pasan por el lote del jueves:
STATUS_DOMICILIACION = "domiciliacion_pendiente_conciliacion"
STATUS_TARJETA = "tarjeta_pendiente_conciliacion"
# Otros documentos:
STATUS_OTRO_DOC = "otro_documento_revisar"
# Gate y cierre:
STATUS_LOTE_DEVUELTO = "lote_devuelto"          # el gate humano rechazo el lote
STATUS_LIBERADA_AL_BANCO = "liberada_al_banco"  # solo tras aprobacion humana
STATUS_CERRADA = "cerrada"                      # pago conciliado y pasivo cancelado

# Tipos de documento (etapa 0)
DOC_INVOICE = "invoice"
DOC_PROFORMA = "proforma_or_advance_request"
DOC_OTHER = "other"

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
class RetencionItem:
    """Documento retenido a la espera de datos internos (NO bloqueado por control)."""
    invoice_id: str
    reason: str               # datos_internos | alta_proveedor | revision_manual
    missing: list[str]
    propuesta: dict[str, Any] # lo que el agente propone; el humano confirma
    detail: str


@dataclass
class TareaConciliacion:
    """Tarea post-pago para metodos que no pasan por el lote del jueves."""
    invoice_id: str
    tipo: str                 # post_debito | extracto_tarjeta
    detail: str


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
    retenciones: list[RetencionItem] = field(default_factory=list)
    tareas: list[TareaConciliacion] = field(default_factory=list)


# ---------- (De)serializacion JSON ----------

def _dec(v: str) -> Decimal:
    return Decimal(v)


def vendor_from_dict(d: dict) -> Vendor:
    d = dict(d)
    d.setdefault("country", "ES")
    d.setdefault("razon_social_confirmada", True)
    d.setdefault("sepa_mandate_ref", None)
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
        cashflow_amount_manual=(
            _dec(d["cashflow_amount_manual"]) if d.get("cashflow_amount_manual") else None
        ),
        tratamiento_iva=d.get("tratamiento_iva", "nacional"),
        metodo_pago=d.get("metodo_pago", "transferencia"),
        menciona_factura_final=d.get("menciona_factura_final", False),
        presupuesto_aprobado=d.get("presupuesto_aprobado"),
        anticipo_pagado=d.get("anticipo_pagado", False),
        factura_final_ref=d.get("factura_final_ref"),
        internal_approver=d.get("internal_approver"),
        cost_center=d.get("cost_center"),
        contract_ref=d.get("contract_ref"),
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
