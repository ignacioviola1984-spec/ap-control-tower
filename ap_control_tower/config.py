"""Configuracion del motor: tolerancias explicitas y reglas de negocio.

Todo umbral que decide hard/soft vive aca, visible y configurable.
El mes demo es junio 2026: tiene exactamente 4 jueves de pago (4, 11, 18, 25).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class EngineConfig:
    # Mes simulado y jueves de pago
    demo_month: str = "2026-06"
    payment_thursdays: tuple[date, ...] = (
        date(2026, 6, 4),
        date(2026, 6, 11),
        date(2026, 6, 18),
        date(2026, 6, 25),
    )

    # C2 Duplicados: casi-duplicado = mismo proveedor + mismo importe,
    # numero distinto, fechas de emision a <= N dias de distancia.
    near_dup_window_days: int = 7

    # C5 Match factura vs OC: cualquier diferencia genera hallazgo.
    # Supera materialidad (porcentaje O absoluto) -> hard (bloquea).
    # Menor a materialidad -> soft (avanza con flag).
    match_materiality_pct: Decimal = Decimal("5")        # % sobre la linea de OC
    match_materiality_abs: Decimal = Decimal("750")      # EUR absolutos
    # Moneda distinta o proveedor distinto al de la OC: siempre hard.
    # BU distinta: soft.

    # Regla de lote: una factura entra al lote del primer jueves ESTRICTAMENTE
    # posterior a su fecha de recepcion, si supero todos los controles hard,
    # esta contabilizada y conciliada. Sin jueves restante en el mes -> proximo ciclo.

    # Moneda base de la demo
    base_currency: str = "EUR"

    # Limites del checker B de lote (validacion del agregado; se usan desde Dia 2)
    batch_max_total: Decimal = Decimal("150000")
    batch_max_per_vendor: Decimal = Decimal("40000")


DEFAULT_CONFIG = EngineConfig()


# Identificadores canonicos de los controles (aparecen en audit trail, UI y evals)
class Controls:
    C0_CLASIFICACION = "C0_CLASIFICACION"
    C1_COMPLETITUD = "C1_COMPLETITUD"
    C2_DUPLICADOS = "C2_DUPLICADOS"
    C3_AUTORIZACION_OC = "C3_AUTORIZACION_OC"
    C4_IMPUTACION = "C4_IMPUTACION"
    C5_MATCH = "C5_MATCH"
    C6_DATOS_BANCARIOS = "C6_DATOS_BANCARIOS"
    C7_CONCILIACION = "C7_CONCILIACION"
    C8_ANTICIPO_SIN_FACTURA_FINAL = "C8_ANTICIPO_SIN_FACTURA_FINAL"
    C9_VENDOR_MASTER = "C9_VENDOR_MASTER"
    C10_GOBIERNO_NON_PO = "C10_GOBIERNO_NON_PO"
    C11_MANDATO_DOMICILIACION = "C11_MANDATO_DOMICILIACION"


CONTROL_NAMES = {
    Controls.C0_CLASIFICACION: "Clasificacion del documento (factura / proforma / otro)",
    Controls.C1_COMPLETITUD: "Completitud documental",
    Controls.C2_DUPLICADOS: "Deteccion de duplicados y casi-duplicados",
    Controls.C3_AUTORIZACION_OC: "Autorizacion de OC (aprobada, vigente, con saldo)",
    Controls.C4_IMPUTACION: "Imputacion contable, BU y tratamiento de IVA",
    Controls.C5_MATCH: "Match factura vs OC con tolerancias",
    Controls.C6_DATOS_BANCARIOS: "Datos bancarios del proveedor vs maestro (transferencias)",
    Controls.C7_CONCILIACION: "Conciliacion pre-pago cashflow vs ERP",
    Controls.C8_ANTICIPO_SIN_FACTURA_FINAL: "Anticipo pagado sin factura final posterior",
    Controls.C9_VENDOR_MASTER: "Completitud del maestro de proveedores",
    Controls.C10_GOBIERNO_NON_PO: "Gobierno non-PO (aprobador + centro de coste + soporte)",
    Controls.C11_MANDATO_DOMICILIACION: "Mandato SEPA registrado para domiciliacion",
}

# Dueno sugerido de la excepcion cuando el control bloquea
EXCEPTION_OWNERS = {
    Controls.C1_COMPLETITUD: "Solicitante / Compras",
    Controls.C2_DUPLICADOS: "AP (verificacion con proveedor)",
    Controls.C3_AUTORIZACION_OC: "Dueno de la OC / Compras",
    Controls.C5_MATCH: "AP + dueno del proyecto",
    Controls.C6_DATOS_BANCARIOS: "Tesoreria + Compliance (verificar por canal independiente)",
    Controls.C7_CONCILIACION: "AP (conciliacion registro operativo vs contable)",
    Controls.C8_ANTICIPO_SIN_FACTURA_FINAL: "Dueno del presupuesto + AP",
    Controls.C11_MANDATO_DOMICILIACION: "Tesoreria (alta de mandato SEPA)",
}

# Tratamientos de IVA admitidos en un asiento propuesto. "no_desglosado" solo
# es posible en proformas (que nunca llegan a contabilizarse como facturas).
ASIENTO_TRATAMIENTOS_FACTURA = ("nacional", "intracomunitario_inversion_sujeto_pasivo")
