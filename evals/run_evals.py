"""Evals del motor: exit 0 = verde, exit != 0 = contrato roto.

Compara la corrida del motor contra data/expected_outputs.json (derivado de
la intencion declarada del dataset, NUNCA de correr el motor). Verifica:
  1. Estado final, control bloqueante, flags y lote de cada factura.
  2. Composicion, cantidad y total de cada lote del jueves.
  3. Resumen agregado (bloqueadas, monto retenido, proximo ciclo).
  4. INVARIANTE-1: la factura con fraude bancario NUNCA esta en un lote.
  5. INVARIANTE-2: 'liberada_al_banco' no es un estado que el pipeline pueda
     emitir (solo el gate humano lo puede crear; se refuerza en el motor
     completo con la maquina de estados del lote).
  6. La cadena de hashes del audit trail verifica.
  7. Determinismo: dos corridas producen resultados identicos.

Uso: python evals/run_evals.py
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower.engine.pipeline import run_month          # noqa: E402
from ap_control_tower.models import load_dataset                # noqa: E402

FRAUD_INVOICE = "INV-024"
FORBIDDEN_PIPELINE_STATUSES = {"liberada_al_banco", "lote_aprobado", "pagada"}

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def snapshot(result) -> dict:
    """Proyeccion comparable de una corrida (sin run_id/timestamps)."""
    return {
        "outcomes": {
            k: (o.status, o.blocking_control, tuple(o.flags),
                o.batch_date.isoformat() if o.batch_date else None)
            for k, o in result.outcomes.items()
        },
        "batches": [(b.batch_date.isoformat(), tuple(sorted(b.invoice_ids)), str(b.total))
                    for b in result.batches],
    }


def main() -> int:
    dataset_path = ROOT / "data" / "synthetic_month.json"
    expected_path = ROOT / "data" / "expected_outputs.json"
    if not dataset_path.exists() or not expected_path.exists():
        print("FAIL  faltan data/synthetic_month.json o data/expected_outputs.json "
              "(generar con: python -m ap_control_tower.dataset_builder)")
        return 1

    with open(expected_path, encoding="utf-8") as f:
        expected = json.load(f)
    dataset = load_dataset(str(dataset_path))
    result, audit = run_month(dataset)

    print("== 1. Por factura: estado / control bloqueante / flags / lote ==")
    for inv_id, exp in expected["per_invoice"].items():
        o = result.outcomes.get(inv_id)
        if o is None:
            check(False, f"{inv_id}: sin resultado del motor")
            continue
        got_batch = o.batch_date.isoformat() if o.batch_date else None
        ok = (o.status == exp["status"]
              and o.blocking_control == exp["blocking_control"]
              and sorted(o.flags) == exp["flags"]
              and got_batch == exp["batch_date"])
        detail = "" if ok else (f" -> motor: {o.status}/{o.blocking_control}/"
                                f"{sorted(o.flags)}/{got_batch} vs esperado: "
                                f"{exp['status']}/{exp['blocking_control']}/"
                                f"{exp['flags']}/{exp['batch_date']}")
        check(ok, f"{inv_id}{detail}")

    print("== 2. Lotes por jueves: composicion y totales ==")
    got_batches = {b.batch_date.isoformat(): b for b in result.batches}
    check(set(got_batches) == set(expected["batches"]),
          f"jueves con lote: {sorted(got_batches)} == {sorted(expected['batches'])}")
    for d, exp_b in expected["batches"].items():
        b = got_batches.get(d)
        if b is None:
            continue
        check(sorted(b.invoice_ids) == exp_b["invoice_ids"],
              f"lote {d}: composicion ({len(b.invoice_ids)} facturas)")
        check(str(b.total) == exp_b["total"],
              f"lote {d}: total EUR {b.total} == {exp_b['total']}")

    print("== 3. Resumen agregado ==")
    s = expected["summary"]
    blocked = [o for o in result.outcomes.values() if o.status == "bloqueada"]
    blocked_amount = sum(
        (i.amount_total for i in dataset.invoices
         if result.outcomes[i.invoice_id].status == "bloqueada"),
        Decimal("0"),
    )
    check(len(result.outcomes) == s["total_invoices"], f"facturas procesadas: {len(result.outcomes)}")
    check(len(blocked) == s["blocked_count"], f"bloqueadas: {len(blocked)}")
    check(str(blocked_amount) == s["blocked_amount"],
          f"monto retenido por bloqueos: EUR {blocked_amount}")
    check(len(result.carryover_ids) == s["carryover_count"],
          f"proximo ciclo: {len(result.carryover_ids)}")

    print("== 4. INVARIANTE-1: el fraude nunca entra a un lote ==")
    in_any_batch = any(FRAUD_INVOICE in b.invoice_ids for b in result.batches)
    fraud_outcome = result.outcomes[FRAUD_INVOICE]
    check(not in_any_batch, f"{FRAUD_INVOICE} fuera de todos los lotes")
    check(fraud_outcome.status == "bloqueada"
          and fraud_outcome.blocking_control == "C6_DATOS_BANCARIOS",
          f"{FRAUD_INVOICE} bloqueada por C6_DATOS_BANCARIOS")
    check(any(e.invoice_id == FRAUD_INVOICE and e.fraud_alert for e in result.exceptions),
          f"{FRAUD_INVOICE} con alerta de fraude en la cola de excepciones")

    print("== 5. INVARIANTE-2: sin liberacion al banco desde el pipeline ==")
    emitted = {o.status for o in result.outcomes.values()}
    check(not (emitted & FORBIDDEN_PIPELINE_STATUSES),
          f"estados emitidos por el pipeline: {sorted(emitted)} (ninguno de liberacion/pago)")

    print("== 6. Audit trail ==")
    check(audit.verify_chain(), f"cadena de hashes verificada ({len(audit.events)} eventos)")
    check(all(ev.run_id == result.run_id and ev.commit == result.commit
              for ev in audit.events), "run_id y commit consistentes en todos los eventos")

    print("== 7. Determinismo ==")
    result2, _ = run_month(dataset)
    check(snapshot(result) == snapshot(result2), "dos corridas -> resultados identicos")

    print()
    if failures:
        print(f"EVALS ROJOS: {len(failures)} fallas")
        return 1
    print("EVALS VERDES: todas las verificaciones pasan (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
