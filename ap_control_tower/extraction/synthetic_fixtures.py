"""Fixtures sinteticas de extraccion v2: los casos que trae la realidad.

Cinco documentos inventados (ningun dato real de ninguna empresa) que cubren
los casos detectados en el analisis de facturas reales:

  EXT-001  proforma / solicitud de anticipo SIN CIF (sin numero fiscal, sin
           IVA desglosado, menciona factura final futura, vencimiento no
           calculable: "al inicio del estudio")
  EXT-002  factura con domiciliacion SEPA (direct debit, sin IBAN visible,
           periodo "cuota JULIO 2026" estructurado)
  EXT-003  factura intracomunitaria con reverse charge (VAT 0%, proveedor NL
           con KVK, referencia "Order ref: ORD-..." que NO es PO)
  EXT-004  factura con IBAN ENMASCARADO (se capturan los digitos visibles,
           iban_enmascarado=true) y PO etiquetada ("Pedido: PO-4471")
  EXT-005  factura con vencimiento en texto "45 days end of month"
           (emision 2026-06-10 -> fin de mes 2026-06-30 + 45 = 2026-08-14)

Genera: doc_texts/*.txt (input del extractor), doc_previews/*.html (visual),
golden_labels.csv (etiquetado humano simulado) y labels_template.csv.

Uso: python -m ap_control_tower.extraction.synthetic_fixtures
"""

from __future__ import annotations

import csv
from pathlib import Path

from .comparator import labels_template_row
from .schema import FIELD_ORDER, empty_document

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "extraction"

BUYER = "Meridia Consulting SL"
BUYER_TAX = "B00000000"


def _doc(**overrides) -> dict:
    d = empty_document()
    d.update(overrides)
    return d


# ----------------------------------------------------------- golden labels
FIXTURES: dict[str, dict] = {
    "EXT-001": _doc(
        document_type="proforma_or_advance_request",
        proveedor_nombre_comercial="Estudio Delfos Investigacion",
        # sin razon social clara y SIN CIF: ambos null a proposito
        cliente_nombre=BUYER,
        fecha_emision="2026-06-05",
        fecha_vencimiento_texto="al inicio del estudio",
        moneda="EUR",
        importe_total="6050.00",
        tratamiento_iva="no_desglosado",
        metodo_pago="transferencia",
        iban="ES2814650100722030876541",
        project_reference="EST-2026-TRK3",
        condiciones_pago="50% de anticipo al inicio del estudio; factura final a la entrega del informe",
    ),
    "EXT-002": _doc(
        document_type="invoice",
        proveedor_nombre_comercial="Nimbus Suscripciones",
        proveedor_razon_social_legal="Nimbus Suscripciones SL",
        proveedor_tax_id="B66123456",
        cliente_nombre=BUYER,
        cliente_tax_id=BUYER_TAX,
        numero_factura="NB-2026-0187",
        fecha_emision="2026-07-01",
        periodo_servicio_desde="2026-07-01",
        periodo_servicio_hasta="2026-07-31",
        moneda="EUR",
        importe_neto="180.00",
        tipo_iva="21",
        importe_iva="37.80",
        importe_total="217.80",
        tratamiento_iva="nacional",
        metodo_pago="domiciliacion_direct_debit",
        condiciones_pago="Pago por domiciliacion bancaria SEPA (mandato NB-4411)",
    ),
    "EXT-003": _doc(
        document_type="invoice",
        proveedor_nombre_comercial="Panelbase Europe",
        proveedor_razon_social_legal="Panelbase Europe BV",
        proveedor_tax_id="NL823456789B01",
        proveedor_registro="KVK 87654321",
        cliente_nombre=BUYER,
        cliente_tax_id="ESB00000000",
        numero_factura="PB-2026-0455",
        fecha_emision="2026-06-12",
        fecha_vencimiento_texto="30 days",
        fecha_vencimiento_calculada="2026-07-12",
        moneda="EUR",
        importe_neto="4200.00",
        tipo_iva="0",
        importe_iva="0.00",
        importe_total="4200.00",
        tratamiento_iva="intracomunitario_inversion_sujeto_pasivo",
        metodo_pago="transferencia",
        iban="NL02ABNA0123456789",
        bic="ABNANL2A",
        project_reference="ORD-2026-114",
        condiciones_pago="30 days",
    ),
    "EXT-004": _doc(
        document_type="invoice",
        proveedor_nombre_comercial="Talleres Graficos Sur",
        proveedor_razon_social_legal="Talleres Graficos Sur SL",
        proveedor_tax_id="B91555666",
        cliente_nombre=BUYER,
        cliente_tax_id=BUYER_TAX,
        numero_factura="TG-2026-311",
        fecha_emision="2026-06-18",
        fecha_vencimiento_texto="15 dias",
        fecha_vencimiento_calculada="2026-07-03",
        moneda="EUR",
        importe_neto="890.00",
        tipo_iva="21",
        importe_iva="186.90",
        importe_total="1076.90",
        tratamiento_iva="nacional",
        metodo_pago="transferencia",
        iban="ES71 **** **** **** **** 3402",
        iban_enmascarado=True,
        po_reference="PO-4471",
        condiciones_pago="15 dias desde emision",
    ),
    "EXT-005": _doc(
        document_type="invoice",
        proveedor_nombre_comercial="Insight Iberia",
        proveedor_razon_social_legal="Insight Partners Iberia SLU",
        proveedor_tax_id="B44777888",
        cliente_nombre=BUYER,
        cliente_tax_id=BUYER_TAX,
        numero_factura="IP-2026/078",
        fecha_emision="2026-06-10",
        fecha_vencimiento_texto="45 days end of month",
        fecha_vencimiento_calculada="2026-08-14",
        periodo_servicio_desde="2026-05-01",
        periodo_servicio_hasta="2026-05-31",
        moneda="EUR",
        importe_neto="5400.00",
        tipo_iva="21",
        importe_iva="1134.00",
        importe_total="6534.00",
        tratamiento_iva="nacional",
        metodo_pago="transferencia",
        iban="ES4321000418450200051332",
        bic="CAIXESBBXXX",
        condiciones_pago="45 days end of month",
    ),
}

# --------------------------------------------------- texto plano (extractor)
DOC_TEXTS = {
    "EXT-001": """PROFORMA / SOLICITUD DE ANTICIPO
Estudio Delfos Investigacion
Madrid

Para: Meridia Consulting SL

Referencia de estudio: EST-2026-TRK3
Fecha: 5 de junio de 2026

Concepto: Anticipo 50% - Estudio tracking de marca Q3 2026
Importe del anticipo: 6.050,00 EUR

Condiciones: 50% de anticipo al inicio del estudio; factura final a la
entrega del informe. Vencimiento: al inicio del estudio.
Forma de pago: transferencia a la cuenta ES28 1465 0100 7220 3087 6541.

Este documento NO es una factura. La factura fiscal, con su numero e IVA
correspondiente, se emitira al finalizar el estudio.""",

    "EXT-002": """NIMBUS SUSCRIPCIONES SL
CIF B66123456
Factura n.: NB-2026-0187
Fecha de emision: 01/07/2026

Cliente: Meridia Consulting SL - CIF B00000000

Concepto: cuota JULIO 2026 - plan Business (10 usuarios)
Base imponible: 180,00 EUR
IVA (21%): 37,80 EUR
TOTAL: 217,80 EUR

Forma de pago: domiciliacion bancaria SEPA. Mandato NB-4411.
El cargo se realizara en la cuenta habitual del cliente.""",

    "EXT-003": """PANELBASE EUROPE BV
VAT: NL823456789B01 - KVK 87654321
Amsterdam, The Netherlands

INVOICE PB-2026-0455
Invoice date: 12 June 2026
Bill to: Meridia Consulting SL - VAT ESB00000000
Order ref: ORD-2026-114

Online panel services - consumer study Spain
Net amount: EUR 4,200.00
VAT 0% - Reverse charge, article 194 EU VAT Directive 2006/112/EC: EUR 0.00
TOTAL: EUR 4,200.00

Payment terms: 30 days
Bank transfer to IBAN NL02 ABNA 0123 4567 89 - BIC ABNANL2A""",

    "EXT-004": """TALLERES GRAFICOS SUR SL
CIF B91555666

FACTURA TG-2026-311
Fecha: 18/06/2026
Cliente: Meridia Consulting SL - CIF B00000000
Pedido: PO-4471

Impresion de material corporativo
Base imponible: 890,00 EUR
IVA 21%: 186,90 EUR
TOTAL: 1.076,90 EUR

Condiciones: 15 dias desde emision.
Transferencia a IBAN: ES71 **** **** **** **** 3402""",

    "EXT-005": """INSIGHT IBERIA
Insight Partners Iberia SLU - CIF B44777888

FACTURA IP-2026/078
Fecha de emision: 10/06/2026
Cliente: Meridia Consulting SL - CIF B00000000

Servicios de consultoria - MAYO 2026 (01/05/2026 a 31/05/2026)
Base imponible: 5.400,00 EUR
IVA (21%): 1.134,00 EUR
TOTAL: 6.534,00 EUR

Payment terms: 45 days end of month
Transferencia: IBAN ES43 2100 0418 4502 0005 1332 - BIC CAIXESBBXXX""",
}

_HTML_SHELL = """<!-- Documento sintetico de extraccion (demo). Ningun dato real. -->
<div style="max-width:720px;margin:24px auto;font-family:Georgia,serif;color:#1a1a1a;
            background:#fff;border:1px solid #d8d8d8;padding:44px 52px;white-space:pre-wrap;
            font-size:13.5px;line-height:1.65;">{body}</div>
"""


def main() -> None:
    texts_dir = DATA_DIR / "doc_texts"
    previews_dir = DATA_DIR / "doc_previews"
    texts_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    for doc_id, text in DOC_TEXTS.items():
        (texts_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        (previews_dir / f"{doc_id}.html").write_text(
            _HTML_SHELL.format(body=text), encoding="utf-8")

    # golden_labels.csv: el etiquetado humano simulado de las 5 fixtures
    def _cell(doc: dict, f: str) -> str:
        v = doc[f]
        if f == "iban_enmascarado":
            return "true" if v else "false"
        if f == "campos_ilegibles":
            return ";".join(v)
        return "" if v is None else str(v)

    with open(DATA_DIR / "golden_labels.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(labels_template_row())
        for doc_id, doc in FIXTURES.items():
            w.writerow([doc_id, f"doc_texts/{doc_id}.txt",
                        *[_cell(doc, f) for f in FIELD_ORDER],
                        "etiquetado sintetico de referencia"])

    # labels_template.csv: solo encabezado, para etiquetar documentos nuevos
    with open(DATA_DIR / "labels_template.csv", "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerow(labels_template_row())

    print(f"OK {len(FIXTURES)} fixtures -> {DATA_DIR}")
    print("   doc_texts/ + doc_previews/ + golden_labels.csv + labels_template.csv")


if __name__ == "__main__":
    main()
