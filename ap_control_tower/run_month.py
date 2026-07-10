"""CLI: corre el mes sintetico completo y muestra el resumen de la corrida.

Uso: python -m ap_control_tower.run_month
Corre el pipeline y los dos checkers de lote; deja cada lote PENDIENTE DE
APROBACION HUMANA (el gate vive en la UI: el CLI jamas aprueba solo).
Escribe runs/<run_id>/audit.jsonl (gitignoreado).
No requiere API keys, red ni servicios externos.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import DEFAULT_CONFIG
from .engine.batch import ESTADO_PENDIENTE_HUMANO, BatchWorkflow
from .engine.pipeline import run_month
from .models import STATUS_BLOQUEADA, load_dataset

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "synthetic_month.json"


def main() -> int:
    if not DATASET.exists():
        print("Dataset ausente. Generalo con: python -m ap_control_tower.dataset_builder")
        return 1
    dataset = load_dataset(str(DATASET))
    result, audit, ctx = run_month(dataset)

    print(f"AP Control Tower | corrida {result.run_id} | commit {result.commit}")
    print(f"Facturas procesadas: {len(result.outcomes)}")
    print()
    blocked = [o for o in result.outcomes.values() if o.status == STATUS_BLOQUEADA]
    print(f"Cola de excepciones ({len(blocked)} bloqueadas, sin intervencion humana):")
    for e in result.exceptions:
        alerta = "  [ALERTA FRAUDE]" if e.fraud_alert else ""
        print(f"  {e.invoice_id} <- {e.control_id} | dueno: {e.owner}{alerta}")
        print(f"      {e.detail}")
    print()
    flagged = {o.invoice_id: o.flags for o in result.outcomes.values() if o.flags}
    print(f"Avanzan con flag soft ({len(flagged)}):")
    for inv_id, flags in sorted(flagged.items()):
        print(f"  {inv_id}: {', '.join(flags)}")
    print()
    print(f"Retenidas a la espera de datos (NO bloqueadas) ({len(result.retenciones)}):")
    for r in result.retenciones:
        prop = (f" | propuesta del agente: {r.propuesta}" if r.propuesta else "")
        print(f"  {r.invoice_id} <- {r.reason}: falta {', '.join(r.missing)}{prop}")
    print()
    print(f"Tareas de conciliacion (metodos fuera del lote) ({len(result.tareas)}):")
    for t in result.tareas:
        print(f"  {t.invoice_id}: {t.tipo} - {t.detail}")
    print()

    print("Lotes de pago: doble sign-off agentico y gate humano:")
    for b in result.batches:
        wf = BatchWorkflow(b, result, ctx, audit, DEFAULT_CONFIG)
        a = wf.run_checker_a()
        line = f"  jueves {b.batch_date.isoformat()}: {b.count} facturas, total EUR {b.total}"
        if not a.ok:
            print(f"{line}\n      checker A DETIENE el lote: {a.detail}")
            continue
        bres = wf.run_checker_b()
        if not bres.ok:
            print(f"{line}\n      checker B DETIENE el lote: {bres.detail}")
            continue
        assert wf.state == ESTADO_PENDIENTE_HUMANO
        print(f"{line}")
        print(f"      sign-off A: {a.detail} [{a.ts}]")
        print(f"      sign-off B: {bres.detail} [{bres.ts}]")
        print("      estado: PENDIENTE DE APROBACION HUMANA (el CLI no libera dinero)")
    print()
    print(f"Proximo ciclo (sin jueves restante): {', '.join(result.carryover_ids) or '-'}")
    print()

    out_dir = ROOT / "runs" / result.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audit.to_jsonl(str(out_dir / "audit.jsonl"))
    print(f"Audit trail: {out_dir / 'audit.jsonl'} ({len(audit.events)} eventos, "
          f"cadena {'VERIFICADA' if audit.verify_chain() else 'ROTA'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
