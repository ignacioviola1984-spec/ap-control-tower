"""Evals de la capa de aplicacion (Fase 3). exit 0 = verde.

Ejercita TODO el flujo de negocio a traves de ap_control_tower.app SIN
Streamlit: prueba que la orquestacion (corrida, gate, revision humana) vive en
la capa de casos de uso y no en la UI. Solo-stdlib + engine; corre en cualquier
entorno.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower import app                                    # noqa: E402
from ap_control_tower.models import (                               # noqa: E402
    STATUS_CERRADA,
    STATUS_LIBERADA_AL_BANCO,
    STATUS_LOTE_DEVUELTO,
    load_dataset,
)

APPROVER = "Aprobadora Demo (apoderada)"
failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def expect_gate_violation(fn, label: str) -> None:
    try:
        fn()
    except app.GateViolation as e:
        check(True, f"{label} -> GateViolation")
    else:
        check(False, f"{label} -> NO levanto GateViolation")


def main() -> int:
    dataset_path = ROOT / "data" / "synthetic_month.json"
    if not dataset_path.exists():
        print("FAIL  falta data/synthetic_month.json")
        return 1

    print("== 1. process_month arma la corrida con checkers (sin Streamlit) ==")
    ds = load_dataset(str(dataset_path))
    run = app.process_month(ds)
    check(set(run) == {"result", "audit", "ctx", "workflows", "closing_reports"},
          "estado de corrida con la forma esperada")
    check(len(run["workflows"]) == len(run["result"].batches),
          f"un workflow por lote ({len(run['workflows'])})")
    check(all(wf.state == app.ESTADO_PENDIENTE_HUMANO
              for wf in run["workflows"].values()),
          "todos los lotes quedan pendientes de aprobacion humana")

    print("== 2. Gate feliz por la capa de aplicacion: aprobar+liberar+cerrar ==")
    for iso, wf in run["workflows"].items():
        decision = app.approve_and_release(run, iso, APPROVER)
        check(decision.approver == APPROVER and wf.state == app.ESTADO_LIBERADO,
              f"lote {iso}: aprobado y liberado por {decision.approver}")
        check(all(run["result"].outcomes[i].status == STATUS_LIBERADA_AL_BANCO
                  for i in wf.batch.invoice_ids),
              f"lote {iso}: facturas en liberada_al_banco")
        report = app.close_batch(run, iso)
        check(not report.exceptions
              and all(run["result"].outcomes[i].status == STATUS_CERRADA
                      for i in wf.batch.invoice_ids),
              f"lote {iso}: cierre concilia sin excepciones")
    check(run["audit"].verify_chain(), "cadena de auditoria verifica tras el gate")

    print("== 3. El gate no se puede saltar desde la capa de aplicacion ==")
    run2 = app.process_month(ds)
    iso2 = next(iter(run2["workflows"]))
    app.reject_batch(run2, iso2, APPROVER, "Revisar prioridad con Tesoreria")
    check(all(run2["result"].outcomes[i].status == STATUS_LOTE_DEVUELTO
              for i in run2["workflows"][iso2].batch.invoice_ids),
          "rechazar devuelve el lote (facturas en lote_devuelto)")
    expect_gate_violation(lambda: app.approve_and_release(run2, iso2, APPROVER),
                          "aprobar+liberar un lote rechazado")

    print("== 4. Revision humana por la capa de aplicacion ==")
    run3 = app.process_month(ds)
    thursdays_before = {b.batch_date.isoformat() for b in run3["result"].batches}
    status14 = app.confirm_internal_data(
        ds, run3, confirmed_by="Revisora Demo", invoice_id="INV-014",
        cost_center="CO-020", internal_approver="Marketing / J. Peralta",
        contract_ref="EMAIL-ENCARGO-2026-05")
    b11 = next(b for b in run3["result"].batches if b.batch_date.isoformat() == "2026-06-11")
    check(status14 == "en_lote" and "INV-014" in b11.invoice_ids,
          "confirmar INV-014 -> entra al lote 11-jun")
    check(run3["workflows"]["2026-06-11"].state == app.ESTADO_PENDIENTE_HUMANO,
          "el lote reabierto volvio a correr checkers y espera el gate")
    check("2026-06-11" in thursdays_before, "el lote 11-jun ya existia (reabierto, no nuevo)")

    print("== 5. Aprobacion de anticipo por la capa de aplicacion ==")
    from dataclasses import replace
    ds_p = type(ds)(vendors=ds.vendors, pos=ds.pos,
                    invoices=[replace(i, presupuesto_aprobado=False)
                              if i.invoice_id == "INV-101" else i for i in ds.invoices])
    run4 = app.process_month(ds_p)
    st_ant = app.approve_anticipo(ds_p, run4, confirmed_by="Revisora Demo",
                                  invoice_id="INV-101")
    check(st_ant == "anticipo_pagado_sin_factura_final",
          "aprobar anticipo -> C8 detecta anticipo pagado sin factura final")
    check(not any("INV-101" in b.invoice_ids for b in run4["result"].batches),
          "el anticipo jamas entra a un lote (INVARIANTE-3)")

    print()
    if failures:
        print(f"CAPA DE APLICACION ROJA: {len(failures)} fallas")
        return 1
    print("CAPA DE APLICACION VERDE: flujo de negocio headless OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
