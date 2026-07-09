"""Renderiza facturas sinteticas clave como documento HTML autocontenido.

Genera data/doc_previews/INV-xxx.html: la "factura del proveedor" que la UI
muestra al lado de los datos extraidos (vista documento -> datos). Todo
inline, sin assets externos, sin red. Datos 100% inventados.

Uso: python -m ap_control_tower.render_invoices
"""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "doc_previews"

# Facturas clave del guion: limpia, soft, sin OC, duplicada, fraude, match hard
KEY_INVOICES = ["INV-005", "INV-009", "INV-014", "INV-023", "INV-024", "INV-025"]

ACCENTS = {
    "V001": "#1f3a5f", "V003": "#0e7466", "V007": "#8a3ffc", "V009": "#b3541e",
}

IVA = Decimal("0.21")

TEMPLATE = """<!-- Documento sintetico generado para la demo AP Control Tower. Datos inventados. -->
<div style="max-width:760px;margin:24px auto;font-family:Georgia,'Times New Roman',serif;
            color:#1a1a1a;background:#fff;border:1px solid #d8d8d8;padding:48px 56px;">
  <table style="width:100%;border-collapse:collapse;">
    <tr>
      <td style="vertical-align:top;">
        <div style="font-size:26px;font-weight:bold;color:{accent};letter-spacing:.5px;">{vendor_name}</div>
        <div style="font-size:12px;color:#555;margin-top:6px;line-height:1.5;">
          {vendor_category}<br>NIF {vendor_tax_id}<br>{vendor_address}
        </div>
      </td>
      <td style="vertical-align:top;text-align:right;">
        <div style="font-size:20px;color:{accent};font-weight:bold;">FACTURA</div>
        <div style="font-size:13px;margin-top:6px;line-height:1.7;">
          N.&ordm; <b>{invoice_number}</b><br>
          Fecha de emisi&oacute;n: <b>{issue_date}</b><br>
          Vencimiento: {terms} d&iacute;as
        </div>
      </td>
    </tr>
  </table>
  <div style="margin-top:28px;padding:14px 18px;background:#f6f6f2;font-size:13px;line-height:1.6;">
    <b>Facturar a:</b><br>Meridia Consulting SL<br>Paseo Imaginario 123, 28000 Madrid<br>NIF B00000000
  </div>
  <table style="width:100%;border-collapse:collapse;margin-top:26px;font-size:13px;">
    <tr style="background:{accent};color:#fff;">
      <th style="text-align:left;padding:9px 12px;">Concepto</th>
      <th style="text-align:right;padding:9px 12px;width:130px;">Importe</th>
    </tr>
    <tr>
      <td style="padding:12px;border-bottom:1px solid #e2e2e2;">{description}{po_note}</td>
      <td style="padding:12px;text-align:right;border-bottom:1px solid #e2e2e2;">{base} &euro;</td>
    </tr>
    <tr><td style="padding:8px 12px;text-align:right;color:#555;">IVA (21%)</td>
        <td style="padding:8px 12px;text-align:right;color:#555;">{iva} &euro;</td></tr>
    <tr><td style="padding:10px 12px;text-align:right;font-weight:bold;font-size:15px;">TOTAL</td>
        <td style="padding:10px 12px;text-align:right;font-weight:bold;font-size:15px;
                   border-top:2px solid {accent};">{total} &euro;</td></tr>
  </table>
  <div style="margin-top:30px;font-size:12.5px;color:#333;line-height:1.7;">
    <b>Forma de pago:</b> transferencia bancaria<br>
    <b>IBAN:</b> <span style="font-family:'Courier New',monospace;">{iban}</span><br>
    Moneda: EUR
  </div>
  <div style="margin-top:36px;padding-top:14px;border-top:1px solid #e2e2e2;
              font-size:10.5px;color:#999;text-align:center;">
    Documento sint&eacute;tico generado para demo. Ning&uacute;n dato real de ninguna empresa.
  </div>
</div>
"""

ADDRESSES = {
    "V001": "Calle Ficticia del Foro 45, 28001 Madrid",
    "V003": "Parque Tecnologico Inventado 7, 41092 Sevilla",
    "V007": "Calle del Prisma Imaginario 12, 08012 Barcelona",
    "V009": "Av. Sintetica 890, 28020 Madrid",
}


def _fmt(d: Decimal) -> str:
    return f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render(inv: dict, vendor: dict) -> str:
    total = Decimal(inv["amount_total"])
    base = (total / (1 + IVA)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    iva = total - base
    po_note = (f"<br><span style='color:#777;font-size:12px;'>Ref. pedido: {inv['po_ref']}</span>"
               if inv["po_ref"] else "")
    return TEMPLATE.format(
        accent=ACCENTS.get(inv["vendor_id"], "#1f3a5f"),
        vendor_name=inv["vendor_name"],
        vendor_category=vendor["category"],
        vendor_tax_id=vendor["tax_id"],
        vendor_address=ADDRESSES.get(inv["vendor_id"], "Calle Inventada 1, 28000 Madrid"),
        invoice_number=inv["invoice_number"],
        issue_date=inv["issue_date"],
        terms=vendor["payment_terms_days"],
        description=inv["description"],
        po_note=po_note,
        base=_fmt(base),
        iva=_fmt(iva),
        total=_fmt(total),
        iban=inv["iban_on_invoice"],
    )


def main() -> None:
    with open(ROOT / "data" / "synthetic_month.json", encoding="utf-8") as f:
        ds = json.load(f)
    vendors = {v["vendor_id"]: v for v in ds["vendors"]}
    invoices = {i["invoice_id"]: i for i in ds["invoices"]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for inv_id in KEY_INVOICES:
        inv = invoices[inv_id]
        html = render(inv, vendors[inv["vendor_id"]])
        path = OUT_DIR / f"{inv_id}.html"
        path.write_text(html, encoding="utf-8")
        print(f"OK {path.name} ({inv['vendor_name']}, {inv['invoice_number']})")


if __name__ == "__main__":
    main()
