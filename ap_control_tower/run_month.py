"""CLI: corre el mes sintetico completo y muestra el resumen de la corrida.

Uso: python -m ap_control_tower.run_month
Escribe runs/<run_id>/audit.jsonl (gitignoreado) y un resumen en consola.
No requiere API keys, red ni servicios externos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .engine.pipeline import run_month
from .models import STATUS_BLOQUEADA, load_dataset

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "synthetic_month.json"


def main() -> int:
    if not DATASET.exists():
        print("Dataset ausente. Generalo con: python -m ap_control_tower.dataset_builder")
        return 1
    dataset = load_dataset(str(DATASET))
    result, audit = run_month(dataset)

    out_dir = ROOT / "runs" / result.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audit.to_jsonl(str(out_dir / "audit.jsonl"))

    print(f"AP Control Tower | corrida {result.run_id} | commit {result.commit}")
    print(f"Facturas procesadas: {len(result.outcomes)}")
    print()
    print("Lotes de pago propuestos (pendientes de aprobacion humana):")
    for b in result.batches:
        print(f"  jueves {b.batch_date.isoformat()}: {b.count} facturas, total EUR {b.total}")
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
    print(f"Proximo ciclo (sin jueves restante): {', '.join(result.carryover_ids) or '-'}")
    print()
    print(f"Audit trail: {out_dir / 'audit.jsonl'} ({len(audit.events)} eventos, "
          f"cadena {'VERIFICADA' if audit.verify_chain() else 'ROTA'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
