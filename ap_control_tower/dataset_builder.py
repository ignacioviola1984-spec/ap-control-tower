"""Constructor del dataset sintetico: el guion de la venta.

Un mes completo (junio 2026, 4 jueves de pago), 36 facturas en EUR, 18
proveedores inventados con mix realista de servicios para una consultora.
NINGUN dato real de ninguna empresa: nombres, NIFs e IBANs son inventados.

Casos plantados (el resto son limpias):
  INV-023  duplicada exacta de INV-005 (mismo proveedor+numero+importe+fecha)
  INV-015  casi-duplicada de INV-007 (mismo proveedor e importe, numero
           distinto, emision a 3 dias)
  INV-014  email sin OC adjunta
  INV-033  OC sin saldo (el fee de la OC ya fue consumido por INV-017)
  INV-024  datos bancarios distintos del maestro (EL FRAUDE, caso estrella)
  INV-025  match fuera de tolerancia grande: +18.3% vs OC (hard)
  INV-009  diferencia menor: +1.69% vs OC (soft, avanza con flag)
  INV-020  diferencia menor: +1.44% vs OC (soft, avanza con flag)

Los expected outputs se derivan de la INTENCION declarada de cada fila (no de
correr el motor): el eval compara motor vs intencion, nunca motor vs si mismo.

Uso: python -m ap_control_tower.dataset_builder
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------- proveedores
# (vendor_id, nombre, NIF, IBAN maestro, banco, plazo dias, intercompany, rubro)
VENDORS = [
    ("V001", "Estudio Lezama Abogados SLP", "B81234561", "ES6100751234510600000021", "Banco Popular Iberico", 30, False, "Asesoria legal"),
    ("V002", "TalentBridge Seleccion SL",   "B82345672", "ES2400811234560600000034", "Banco del Comercio",    30, False, "Seleccion de personal"),
    ("V003", "Nubia Cloud Services SL",     "B83456783", "ES4001821234570600000047", "Banco Azul",            30, False, "Cloud y hosting"),
    ("V004", "Ofisur Coworking SA",         "A84567894", "ES1800491234580600000050", "Banco del Sur",         15, False, "Alquiler de oficina"),
    ("V005", "Viajes Meridiano SL",         "B85678905", "ES9321001234590600000063", "Caja Meridional",       15, False, "Agencia de viajes"),
    ("V006", "Telco Iberica SA",            "A86789016", "ES7100301234600600000076", "Banco Central Iberico", 30, False, "Telecomunicaciones"),
    ("V007", "Kreativa Estudio SL",         "B87890127", "ES4920381234610600000089", "Banco de Levante",      30, False, "Diseno y branding"),
    ("V008", "DataPulse Analytics SL",      "B88901238", "ES2700721234620600000092", "Banco Digital SA",      30, False, "Licencias SaaS"),
    ("V009", "Consultores Andino SL",       "B89012349", "ES0500611234630600000105", "Banco Andino Iberia",   30, False, "Subcontratacion consultoria"),
    ("V010", "Beatriz Roldan Casas",        "51234567Q", "ES8320851234640600000118", "Caja Rural del Este",   15, False, "Consultora freelance"),
    ("V011", "Grupo Meridia UK Ltd",        "GB-990011", "GB29NWBK60161331926819",   "NatWest (UK)",          45, True,  "Intercompany: management fee"),
    ("V012", "Meridia Advisory Mexico SA",  "MX-880022", "ES6114651234660600000134", "Banco Iberico Global",  45, True,  "Intercompany: shared services"),
    ("V013", "Limpiezas Aurora SL",         "B90123450", "ES3900191234670600000147", "Banco del Norte",       30, False, "Limpieza de oficina"),
    ("V014", "Seguros Atlas SA",            "A91234561", "ES1700081234680600000150", "Banco Asegurador",      30, False, "Seguros de empresa"),
    ("V015", "Academia Delta Formacion SL", "B92345672", "ES9502391234690600000163", "Banco de Formento",     30, False, "Formacion"),
    ("V016", "Gestoria Fuentes SL",         "B93456783", "ES7301281234700600000176", "Banco Fiduciario",      15, False, "Gestoria y nominas"),
    ("V017", "Impresiones Rapidas SL",      "B94567894", "ES5120481234710600000189", "Caja Grafica",          15, False, "Imprenta"),
    ("V018", "Mobiliario Norte SL",         "B95678905", "ES2900731234720600000192", "Banco del Norte",       30, False, "Mobiliario de oficina"),
]

# IBAN falso que aparece en la factura del caso de fraude (V003 Nubia):
FRAUD_IBAN = "ES1714651009120038466210"

# ---------------------------------------------------------------- ordenes de compra
# (po_id, vendor, proyecto, GL, categoria gestion, autorizado, [(linea, desc, importe)])
POS = [
    ("PO-2026-012", "V004", "CO-001", "621000", "Instalaciones y oficina", "109680.00",
     [("alquiler-junio", "Alquiler oficina Madrid - junio 2026", "8500.00"),
      ("salas-parking-junio", "Salas de reunion y parking - junio 2026", "640.00")]),
    ("PO-2026-019", "V006", "CO-001", "628000", "IT y sistemas", "10800.00",
     [("fibra-fijo-junio", "Fibra y telefonia fija - junio 2026", "455.90"),
      ("moviles-junio", "Lineas moviles equipo - junio 2026", "389.20")]),
    ("PO-2026-009", "V016", "CO-001", "623000", "Overhead general", "11400.00",
     [("gestoria-mayo", "Gestoria laboral y fiscal - mayo 2026", "950.00"),
      ("gestoria-junio", "Gestoria laboral y fiscal - junio 2026", "950.00")]),
    ("PO-2026-022", "V013", "CO-001", "629000", "Instalaciones y oficina", "8900.00",
     [("limpieza-junio", "Limpieza oficina - junio 2026", "680.00"),
      ("limpieza-extra-junio", "Limpieza extraordinaria post evento", "215.00")]),
    ("PO-2026-050", "V009", "TD-410", "623100", "Coste directo de proyecto", "40000.00",
     [("sprint-mayo", "Equipo consultores - sprint mayo", "11200.00"),
      ("sprint-junio-1", "Equipo consultores - sprint junio (1a quincena)", "9800.00"),
      ("sprint-junio-2", "Equipo consultores - sprint junio (2a quincena)", "8600.00")]),
    ("PO-2026-089", "V008", "CO-014", "628000", "IT y sistemas", "9000.00",
     [("licencias-junio", "Licencias plataforma analytics - junio", "1850.00"),
      ("modulo-reporting", "Modulo adicional de reporting", "2090.00"),
      ("licencias-adicionales", "5 licencias adicionales", "740.00")]),
    ("PO-2026-061", "V010", "CN-215", "623100", "Coste directo de proyecto", "25000.00",
     [("fase-1", "Diagnostico operaciones retail - fase 1", "6400.00"),
      ("fase-2", "Rediseno de procesos - fase 2", "5900.00")]),
    ("PO-2026-076", "V005", "CO-016", "624000", "Viajes", "30000.00",
     [("viaje-cliente-industrial", "Viajes equipo proyecto industrial", "2340.50"),
      ("viaje-taller-bcn", "Desplazamiento taller Barcelona", "890.75"),
      ("viaje-comite-lisboa", "Comite de direccion Lisboa", "1467.30")]),
    ("PO-2026-041", "V001", "CO-005", "623000", "Overhead general", "42480.00",
     [("iguala-junio", "Iguala asesoria legal - junio 2026", "3540.00")]),
    # Autorizado > hito para que el caso plantado de match (INV-025) bloquee en
    # C5 por tolerancia y no antes en C3 por saldo.
    ("PO-2026-058", "V001", "FS-120", "623000", "Coste directo de proyecto", "10000.00",
     [("asesoria-ma", "Asesoria legal operacion M&A - hito 1", "5200.00")]),
    ("PO-2026-071", "V018", "CO-001", "629000", "Instalaciones y oficina", "9000.00",
     [("entrega-1", "Mobiliario puestos flexibles - entrega 1", "6800.00"),
      ("entrega-2", "Mobiliario puestos flexibles - entrega 2", "2200.00")]),
    ("PO-2026-005", "V011", "CO-002", "623900", "Overhead general", "48000.00",
     [("mgmt-fee-q2", "Management fee grupo - Q2 2026", "12000.00"),
      ("refacturacion-gastos-q2", "Refacturacion gastos de grupo - Q2", "2450.00")]),
    ("PO-2026-006", "V012", "CO-003", "623900", "Overhead general", "22400.00",
     [("shared-services-junio", "Shared services - junio 2026", "5600.00")]),
    ("PO-2026-068", "V015", "CO-030", "629100", "Personas y talento", "6000.00",
     [("programa-consultores", "Programa formacion consultores junior", "1780.00"),
      ("taller-datos", "Taller de analisis de datos", "960.00")]),
    ("PO-2026-014", "V014", "CO-001", "625000", "Overhead general", "4941.60",
     [("prima-t3", "Prima trimestral RC profesional - T3", "1235.40")]),
    ("PO-2026-102", "V003", "CO-014", "628000", "IT y sistemas", "18000.00",
     [("cloud-mayo", "Servicios cloud - mayo 2026", "1420.00")]),
    ("PO-2026-117", "V003", "CO-014", "628000", "IT y sistemas", "9340.00",
     [("migracion-datacenter", "Migracion a datacenter secundario", "9340.00")]),
    ("PO-2026-033", "V002", "CO-030", "623000", "Personas y talento", "7500.00",
     [("fee-consultor-senior", "Fee seleccion consultor senior", "7500.00")]),
    ("PO-2026-095", "V017", "CO-020", "629000", "Marketing y ventas", "1500.00",
     [("material-propuestas", "Impresion material de propuestas", "312.60")]),
]

# ---------------------------------------------------------------- facturas
# Cada fila declara su intencion (expected) ademas de sus datos:
# (id, vendor, numero, emision, recepcion, importe, descripcion,
#  po, linea, iban_override, tiene_pdf_oc, caso,
#  expected_status, blocking_control, flags, batch)
INVOICES = [
    # --- Lote jueves 2026-06-04 (recibidas 1-3 jun) ---
    ("INV-001", "V004", "OF-2026-0601", "2026-05-28", "2026-06-01", "8500.00",
     "Alquiler oficina Madrid - junio 2026", "PO-2026-012", "alquiler-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-04"),
    ("INV-002", "V006", "TI-2026-06-77812", "2026-05-29", "2026-06-01", "455.90",
     "Fibra y telefonia fija - junio 2026", "PO-2026-019", "fibra-fijo-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-04"),
    ("INV-003", "V016", "GF-2026-05", "2026-05-30", "2026-06-02", "950.00",
     "Gestoria laboral y fiscal - mayo 2026", "PO-2026-009", "gestoria-mayo", None, True,
     "limpia", "en_lote", None, [], "2026-06-04"),
    ("INV-004", "V013", "LA-06-2026", "2026-05-31", "2026-06-02", "680.00",
     "Limpieza oficina - junio 2026", "PO-2026-022", "limpieza-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-04"),
    ("INV-005", "V009", "CA-2026-0507", "2026-05-31", "2026-06-03", "11200.00",
     "Equipo consultores - sprint mayo", "PO-2026-050", "sprint-mayo", None, True,
     "limpia (luego llega su duplicada: INV-023)", "en_lote", None, [], "2026-06-04"),

    # --- Lote jueves 2026-06-11 (recibidas 4-10 jun) ---
    ("INV-006", "V008", "DP-INV-3301", "2026-06-01", "2026-06-04", "1850.00",
     "Licencias plataforma analytics - junio", "PO-2026-089", "licencias-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-11"),
    ("INV-007", "V010", "2026-27", "2026-06-04", "2026-06-05", "6400.00",
     "Diagnostico operaciones retail - fase 1", "PO-2026-061", "fase-1", None, True,
     "limpia (luego llega su casi-duplicada: INV-015)", "en_lote", None, [], "2026-06-11"),
    ("INV-008", "V005", "VM-88412", "2026-06-03", "2026-06-05", "2340.50",
     "Viajes equipo proyecto industrial", "PO-2026-076", "viaje-cliente-industrial", None, True,
     "limpia", "en_lote", None, [], "2026-06-11"),
    ("INV-009", "V001", "F-2026/231", "2026-06-05", "2026-06-08", "3600.00",
     "Iguala asesoria legal - junio 2026", "PO-2026-041", "iguala-junio", None, True,
     "PLANTADA soft: +60.00 EUR (+1.69%) vs OC, bajo materialidad",
     "en_lote", None, ["MATCH_TOLERANCIA_MENOR"], "2026-06-11"),
    ("INV-010", "V018", "MN-2026-0088", "2026-06-05", "2026-06-08", "6800.00",
     "Mobiliario puestos flexibles - entrega 1", "PO-2026-071", "entrega-1", None, True,
     "limpia", "en_lote", None, [], "2026-06-11"),
    ("INV-011", "V011", "GM-UK-2026-Q2-07", "2026-06-08", "2026-06-09", "12000.00",
     "Management fee grupo - Q2 2026", "PO-2026-005", "mgmt-fee-q2", None, True,
     "limpia intercompany (flag informativo)", "en_lote", None, ["INTERCOMPANY"], "2026-06-11"),
    ("INV-012", "V015", "AD-2026-118", "2026-06-08", "2026-06-09", "1780.00",
     "Programa formacion consultores junior", "PO-2026-068", "programa-consultores", None, True,
     "limpia", "en_lote", None, [], "2026-06-11"),
    ("INV-013", "V014", "SA-PRIMA-2026-T3", "2026-06-09", "2026-06-10", "1235.40",
     "Prima trimestral RC profesional - T3", "PO-2026-014", "prima-t3", None, True,
     "limpia", "en_lote", None, [], "2026-06-11"),

    # --- Bloqueadas semana del 8-12 jun ---
    ("INV-014", "V007", "KR-2026-041", "2026-06-09", "2026-06-10", "4850.00",
     "Rediseno identidad visual y plantillas", None, None, None, False,
     "PLANTADA: email sin OC adjunta -> bloqueo por completitud",
     "bloqueada", "C1_COMPLETITUD", [], None),
    ("INV-015", "V010", "2026-31", "2026-06-07", "2026-06-12", "6400.00",
     "Diagnostico operaciones retail - fase 1", "PO-2026-061", "fase-1", None, True,
     "PLANTADA: casi-duplicada de INV-007 (mismo proveedor e importe, numero distinto, emision a 3 dias)",
     "bloqueada", "C2_DUPLICADOS", [], None),

    # --- Lote jueves 2026-06-18 (recibidas 11-17 jun) ---
    ("INV-016", "V003", "NB-26-0598", "2026-06-10", "2026-06-11", "1420.00",
     "Servicios cloud - mayo 2026", "PO-2026-102", "cloud-mayo", None, True,
     "limpia (establece a Nubia como proveedor habitual antes del fraude)",
     "en_lote", None, [], "2026-06-18"),
    ("INV-017", "V002", "TB-260118", "2026-06-11", "2026-06-12", "7500.00",
     "Fee seleccion consultor senior", "PO-2026-033", "fee-consultor-senior", None, True,
     "limpia (consume todo el saldo de la OC; luego llega INV-033)",
     "en_lote", None, [], "2026-06-18"),
    ("INV-018", "V005", "VM-88515", "2026-06-12", "2026-06-15", "890.75",
     "Desplazamiento taller Barcelona", "PO-2026-076", "viaje-taller-bcn", None, True,
     "limpia", "en_lote", None, [], "2026-06-18"),
    ("INV-019", "V009", "CA-2026-0533", "2026-06-12", "2026-06-15", "9800.00",
     "Equipo consultores - sprint junio (1a quincena)", "PO-2026-050", "sprint-junio-1", None, True,
     "limpia", "en_lote", None, [], "2026-06-18"),
    ("INV-020", "V008", "DP-INV-3342", "2026-06-15", "2026-06-16", "2120.00",
     "Modulo adicional de reporting", "PO-2026-089", "modulo-reporting", None, True,
     "PLANTADA soft: +30.00 EUR (+1.44%) vs OC, bajo materialidad",
     "en_lote", None, ["MATCH_TOLERANCIA_MENOR"], "2026-06-18"),
    ("INV-021", "V017", "IR-10233", "2026-06-15", "2026-06-16", "312.60",
     "Impresion material de propuestas", "PO-2026-095", "material-propuestas", None, True,
     "limpia", "en_lote", None, [], "2026-06-18"),
    ("INV-022", "V012", "MX-2026-0630", "2026-06-16", "2026-06-17", "5600.00",
     "Shared services - junio 2026", "PO-2026-006", "shared-services-junio", None, True,
     "limpia intercompany (flag informativo)", "en_lote", None, ["INTERCOMPANY"], "2026-06-18"),

    # --- Bloqueadas semana del 15-17 jun ---
    ("INV-023", "V009", "CA-2026-0507", "2026-05-31", "2026-06-15", "11200.00",
     "Equipo consultores - sprint mayo", "PO-2026-050", "sprint-mayo", None, True,
     "PLANTADA: duplicada EXACTA de INV-005 (proveedor+numero+importe+fecha); reenvio del proveedor",
     "bloqueada", "C2_DUPLICADOS", [], None),
    ("INV-024", "V003", "NB-26-0644", "2026-06-15", "2026-06-16", "9340.00",
     "Migracion a datacenter secundario", "PO-2026-117", "migracion-datacenter", FRAUD_IBAN, True,
     "PLANTADA: EL FRAUDE. Todo matchea perfecto, pero el IBAN de la factura no es el del maestro",
     "bloqueada", "C6_DATOS_BANCARIOS", [], None),
    ("INV-025", "V001", "F-2026/244", "2026-06-16", "2026-06-17", "6150.00",
     "Asesoria legal operacion M&A", "PO-2026-058", "asesoria-ma", None, True,
     "PLANTADA: match fuera de tolerancia grande: +950.00 EUR (+18.27%) vs OC -> hard",
     "bloqueada", "C5_MATCH", [], None),

    # --- Lote jueves 2026-06-25 (recibidas 18-24 jun) ---
    ("INV-026", "V004", "OF-2026-0615", "2026-06-17", "2026-06-18", "640.00",
     "Salas de reunion y parking - junio 2026", "PO-2026-012", "salas-parking-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),
    ("INV-027", "V006", "TI-2026-06-79034", "2026-06-18", "2026-06-19", "389.20",
     "Lineas moviles equipo - junio 2026", "PO-2026-019", "moviles-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),
    ("INV-028", "V010", "2026-33", "2026-06-18", "2026-06-19", "5900.00",
     "Rediseno de procesos - fase 2", "PO-2026-061", "fase-2", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),
    ("INV-029", "V005", "VM-88601", "2026-06-19", "2026-06-22", "1467.30",
     "Comite de direccion Lisboa", "PO-2026-076", "viaje-comite-lisboa", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),
    ("INV-030", "V015", "AD-2026-129", "2026-06-19", "2026-06-22", "960.00",
     "Taller de analisis de datos", "PO-2026-068", "taller-datos", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),
    ("INV-031", "V008", "DP-INV-3367", "2026-06-22", "2026-06-23", "740.00",
     "5 licencias adicionales", "PO-2026-089", "licencias-adicionales", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),
    ("INV-032", "V013", "LA-06-2026-EXT", "2026-06-22", "2026-06-23", "215.00",
     "Limpieza extraordinaria post evento", "PO-2026-022", "limpieza-extra-junio", None, True,
     "limpia", "en_lote", None, [], "2026-06-25"),

    # --- Bloqueada semana del 22-24 jun ---
    ("INV-033", "V002", "TB-260131", "2026-06-19", "2026-06-22", "7500.00",
     "Fee seleccion consultor senior", "PO-2026-033", "fee-consultor-senior", None, True,
     "PLANTADA: OC sin saldo (INV-017 ya consumio los 7500.00 autorizados). Emision a 8 dias de INV-017: fuera de la ventana de casi-duplicados, bloquea C3",
     "bloqueada", "C3_AUTORIZACION_OC", [], None),

    # --- Recibidas 25-30 jun: sin jueves restante -> proximo ciclo ---
    ("INV-034", "V016", "GF-2026-06", "2026-06-24", "2026-06-25", "950.00",
     "Gestoria laboral y fiscal - junio 2026", "PO-2026-009", "gestoria-junio", None, True,
     "limpia, programada para el primer jueves de julio", "proximo_ciclo", None, [], None),
    ("INV-035", "V009", "CA-2026-0561", "2026-06-26", "2026-06-29", "8600.00",
     "Equipo consultores - sprint junio (2a quincena)", "PO-2026-050", "sprint-junio-2", None, True,
     "limpia, programada para el primer jueves de julio", "proximo_ciclo", None, [], None),
    ("INV-036", "V011", "GM-UK-2026-Q2-11", "2026-06-29", "2026-06-30", "2450.00",
     "Refacturacion gastos de grupo - Q2", "PO-2026-005", "refacturacion-gastos-q2", None, True,
     "limpia intercompany, programada para el primer jueves de julio",
     "proximo_ciclo", None, ["INTERCOMPANY"], None),
]


def build_dataset() -> dict:
    vendor_iban = {v[0]: v[3] for v in VENDORS}
    vendor_name = {v[0]: v[1] for v in VENDORS}
    po_project = {p[0]: p[2] for p in POS}
    return {
        "meta": {
            "title": "AP Control Tower - dataset sintetico junio 2026",
            "disclaimer": "Datos 100% sinteticos. Ningun dato real de ninguna empresa.",
            "month": "2026-06",
            "currency": "EUR",
            "payment_thursdays": ["2026-06-04", "2026-06-11", "2026-06-18", "2026-06-25"],
            "buyer": "Meridia Consulting SL (nombre inventado de la consultora)",
        },
        "vendors": [
            {
                "vendor_id": v[0], "name": v[1], "tax_id": v[2], "iban": v[3],
                "bank_name": v[4], "payment_terms_days": v[5],
                "intercompany": v[6], "category": v[7],
            }
            for v in VENDORS
        ],
        "purchase_orders": [
            {
                "po_id": p[0], "vendor_id": p[1], "project_code": p[2],
                "gl_account": p[3], "mgmt_category": p[4], "currency": "EUR",
                "status": "aprobada",
                "valid_from": "2026-01-01", "valid_to": "2026-12-31",
                "amount_authorized": p[5],
                "lines": [
                    {"line_id": l[0], "description": l[1], "amount": l[2]}
                    for l in p[6]
                ],
            }
            for p in POS
        ],
        "invoices": [
            {
                "invoice_id": r[0], "vendor_id": r[1],
                "vendor_name": vendor_name[r[1]],
                "invoice_number": r[2], "issue_date": r[3], "received_date": r[4],
                "currency": "EUR", "amount_total": r[5], "description": r[6],
                "po_ref": r[7], "po_line_ref": r[8],
                "iban_on_invoice": r[9] if r[9] else vendor_iban[r[1]],
                "has_invoice_pdf": True, "has_po_pdf": r[10],
                "project_code": po_project.get(r[7]) if r[7] else None,
                "case_note": r[11],
            }
            for r in INVOICES
        ],
    }


def build_expected() -> dict:
    """Expected outputs derivados de la intencion declarada por fila."""
    per_invoice = {}
    batches: dict[str, dict] = {}
    blocked_amount = Decimal("0")
    for r in INVOICES:
        inv_id, amount, status, blocking, flags, batch = r[0], Decimal(r[5]), r[12], r[13], r[14], r[15]
        per_invoice[inv_id] = {
            "status": status,
            "blocking_control": blocking,
            "flags": sorted(flags),
            "batch_date": batch,
        }
        if status == "en_lote":
            b = batches.setdefault(batch, {"invoice_ids": [], "total": Decimal("0")})
            b["invoice_ids"].append(inv_id)
            b["total"] += amount
        elif status == "bloqueada":
            blocked_amount += amount

    total_paid = sum((b["total"] for b in batches.values()), Decimal("0"))
    return {
        "meta": {
            "source": "Derivado de la intencion declarada del dataset (no de correr el motor).",
            "month": "2026-06",
        },
        "per_invoice": per_invoice,
        "batches": {
            d: {
                "invoice_ids": sorted(b["invoice_ids"]),
                "count": len(b["invoice_ids"]),
                "total": str(b["total"]),
            }
            for d, b in sorted(batches.items())
        },
        "summary": {
            "total_invoices": len(INVOICES),
            "blocked_count": sum(1 for r in INVOICES if r[12] == "bloqueada"),
            "blocked_amount": str(blocked_amount),
            "in_batches_count": sum(1 for r in INVOICES if r[12] == "en_lote"),
            "in_batches_total": str(total_paid),
            "carryover_count": sum(1 for r in INVOICES if r[12] == "proximo_ciclo"),
            "soft_flagged_ids": sorted(r[0] for r in INVOICES if r[14]),
        },
        "invariants": [
            "INVARIANTE-1: la factura con fraude bancario (INV-024) NUNCA aparece en un lote de pago.",
            "INVARIANTE-2: el estado 'liberada_al_banco' es inalcanzable sin aprobacion humana registrada.",
        ],
        "planted_cases": {
            r[0]: r[11] for r in INVOICES if r[11].startswith("PLANTADA")
        },
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ds_path = DATA_DIR / "synthetic_month.json"
    ex_path = DATA_DIR / "expected_outputs.json"
    with open(ds_path, "w", encoding="utf-8") as f:
        json.dump(build_dataset(), f, ensure_ascii=False, indent=2)
    with open(ex_path, "w", encoding="utf-8") as f:
        json.dump(build_expected(), f, ensure_ascii=False, indent=2)
    print(f"OK dataset -> {ds_path}")
    print(f"OK expected -> {ex_path}")


if __name__ == "__main__":
    main()
