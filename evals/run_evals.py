"""Evals del motor: exit 0 = verde, exit != 0 = contrato roto.

Compara la corrida del motor contra data/expected_outputs.json (derivado de
la intencion declarada del dataset, NUNCA de correr el motor). Verifica:
   1. Estado final, control bloqueante, flags y lote de cada factura.
   2. Composicion, cantidad y total de cada lote del jueves.
   3. Resumen agregado (bloqueadas, monto retenido, proximo ciclo).
   4. INVARIANTE-1: la factura con fraude bancario NUNCA esta en un lote.
   5. INVARIANTE-2 (pipeline): ningun estado de liberacion/pago sale del pipeline.
   6. La cadena de hashes del audit trail verifica.
   7. Determinismo: dos corridas producen resultados identicos.
   8. Gate feliz: sign-off A + sign-off B + aprobacion humana con nombre ->
      liberacion -> cierre concilia pago vs pasivo sin excepciones.
   9. INVARIANTE-2 (duro): liberar sin aprobar, aprobar sin sign-offs, aprobar
      sin nombre y cerrar sin liberar levantan GateViolation.
  10. Tampering: si el estado contable de una factura cambia despues de armar
      el lote, el checker A lo detecta y detiene el lote.
  11. Limites del agregado: un limite por proveedor mas chico hace que el
      checker B detenga el lote.
  12. Rechazo humano: devuelve el lote, las facturas quedan en lote_devuelto
      y la liberacion posterior es imposible.
  13. Password gate: comparacion server-side correcta; el password nunca
      aparece en el codigo del repo.
  14. La app ARRANCA sin API keys y sin red externa: se lanza streamlit en un
      subproceso con un entorno minimo (solo AP_DEMO_PASSWORD) en un puerto
      libre elegido al azar, y el health endpoint responde ok.

Uso: python evals/run_evals.py            (14 grupos)
     python evals/run_evals.py --sin-app  (salta el grupo 14, p. ej. en CI sin GUI)
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_control_tower.config import DEFAULT_CONFIG                          # noqa: E402
from ap_control_tower.engine.batch import (                                 # noqa: E402
    ESTADO_DETENIDO,
    ESTADO_LIBERADO,
    ESTADO_PENDIENTE_HUMANO,
    ESTADO_RECHAZADO,
    BatchWorkflow,
    GateViolation,
)
from ap_control_tower.engine.closing import close_batch                     # noqa: E402
from ap_control_tower.engine.pipeline import run_month                      # noqa: E402
from ap_control_tower.models import (                                       # noqa: E402
    STATUS_CERRADA,
    STATUS_LIBERADA_AL_BANCO,
    STATUS_LOTE_DEVUELTO,
    load_dataset,
)

FRAUD_INVOICE = "INV-024"
FORBIDDEN_PIPELINE_STATUSES = {"liberada_al_banco", "lote_aprobado", "pagada", "cerrada"}
APPROVER = "Aprobadora Demo (apoderada)"

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def expect_violation(fn, label: str) -> None:
    try:
        fn()
    except GateViolation as e:
        check(True, f"{label} -> GateViolation: {e}")
    else:
        check(False, f"{label} -> NO levanto GateViolation")


def _boot_app_check(timeout_s: float = 60.0) -> bool:
    """Lanza streamlit en subproceso con entorno minimo y verifica el health.

    - Puerto: uno libre elegido por el SO (nada hardcodeado), pasado por CLI.
    - Entorno: PATH/SYSTEMROOT del sistema + AP_DEMO_PASSWORD. Ninguna API key.
    - Red: solo localhost (el health endpoint propio de streamlit).
    """
    import os
    import socket
    import subprocess
    import time
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "COMSPEC",
                    "PATHEXT", "WINDIR", "HOME", "USERPROFILE", "APPDATA",
                    "LOCALAPPDATA", "PROGRAMDATA", "LANG")}
    env["AP_DEMO_PASSWORD"] = "eval-arranque"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
         "--server.port", str(port), "--server.address", "127.0.0.1",
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout_s
    ok = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break  # el proceso murio: arranque fallido
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200 and resp.read().strip() == b"ok":
                        ok = True
                        break
            except OSError:
                time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return ok


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
    result, audit, ctx = run_month(dataset)

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

    print("== 5. INVARIANTE-2 (pipeline): sin liberacion al banco desde el pipeline ==")
    emitted = {o.status for o in result.outcomes.values()}
    check(not (emitted & FORBIDDEN_PIPELINE_STATUSES),
          f"estados emitidos por el pipeline: {sorted(emitted)} (ninguno de liberacion/pago)")

    print("== 6. Audit trail ==")
    check(audit.verify_chain(), f"cadena de hashes verificada ({len(audit.events)} eventos)")
    check(all(ev.run_id == result.run_id and ev.commit == result.commit
              for ev in audit.events), "run_id y commit consistentes en todos los eventos")

    print("== 7. Determinismo ==")
    r2, _, _ = run_month(dataset)
    check(snapshot(result) == snapshot(r2), "dos corridas -> resultados identicos")

    print("== 8. Gate feliz: sign-offs + aprobacion humana + liberacion + cierre ==")
    for b in result.batches:
        wf = BatchWorkflow(b, result, ctx, audit, DEFAULT_CONFIG)
        a = wf.run_checker_a()
        check(a.ok, f"lote {b.batch_date}: sign-off checker A")
        bres = wf.run_checker_b()
        check(bres.ok, f"lote {b.batch_date}: sign-off checker B")
        check(wf.state == ESTADO_PENDIENTE_HUMANO,
              f"lote {b.batch_date}: pendiente de aprobacion humana")
        decision = wf.approve(APPROVER)
        check(decision.approver == APPROVER and decision.ts != "",
              f"lote {b.batch_date}: aprobacion registra nombre y timestamp")
        wf.release_to_bank()
        check(wf.state == ESTADO_LIBERADO, f"lote {b.batch_date}: liberado al banco")
        check(all(result.outcomes[i].status == STATUS_LIBERADA_AL_BANCO
                  for i in b.invoice_ids),
              f"lote {b.batch_date}: facturas en liberada_al_banco")
        report = close_batch(wf, ctx, audit)
        check(not report.exceptions and report.liabilities_cancelled == len(b.invoice_ids),
              f"lote {b.batch_date}: cierre concilia {report.liabilities_cancelled} pagos "
              f"vs pasivos sin excepciones (EUR {report.total_paid})")
        check(all(result.outcomes[i].status == STATUS_CERRADA for i in b.invoice_ids),
              f"lote {b.batch_date}: facturas cerradas")
    gate_events = [ev for ev in audit.events if ev.action == "aprobacion-lote"]
    check(len(gate_events) == len(result.batches)
          and all(ev.evidence.get("aprobador") == APPROVER for ev in gate_events),
          "audit trail: una aprobacion humana con nombre por lote")
    check(audit.verify_chain(), "cadena de hashes sigue verificada tras el gate y el cierre")

    print("== 9. INVARIANTE-2 (duro): el gate no se puede saltar ==")
    r9, a9, c9 = run_month(dataset)
    b9 = r9.batches[0]
    wf9 = BatchWorkflow(b9, r9, c9, a9, DEFAULT_CONFIG)
    expect_violation(wf9.release_to_bank, "liberar un lote recien propuesto")
    expect_violation(lambda: wf9.approve(APPROVER), "aprobar sin ningun sign-off")
    wf9.run_checker_a()
    expect_violation(lambda: wf9.approve(APPROVER), "aprobar solo con el sign-off A")
    wf9.run_checker_b()
    expect_violation(lambda: wf9.approve("   "), "aprobar sin nombre de aprobador")
    expect_violation(wf9.release_to_bank, "liberar aun pendiente de aprobacion humana")
    check(all(r9.outcomes[i].status != STATUS_LIBERADA_AL_BANCO for i in b9.invoice_ids),
          "ninguna factura llego a liberada_al_banco en los intentos invalidos")
    wf9.approve(APPROVER)
    expect_violation(lambda: close_batch(wf9, c9, a9), "cerrar un lote aprobado sin liberar")
    wf9.release_to_bank()
    check(wf9.state == ESTADO_LIBERADO, "el flujo correcto sigue funcionando tras los intentos")

    print("== 10. Tampering: checker A detiene el lote ==")
    r10, a10, c10 = run_month(dataset)
    b10 = r10.batches[0]
    victim = b10.invoice_ids[0]
    c10.erp[victim]["amount"] = c10.erp[victim]["amount"] + Decimal("100")
    wf10 = BatchWorkflow(b10, r10, c10, a10, DEFAULT_CONFIG)
    a_sign = wf10.run_checker_a()
    check(not a_sign.ok and wf10.state == ESTADO_DETENIDO,
          f"pasivo adulterado en {victim} (+100) -> checker A detiene el lote")
    expect_violation(lambda: wf10.approve(APPROVER), "aprobar un lote detenido por checker")

    print("== 11. Limites del agregado: checker B detiene el lote ==")
    r11, a11, c11 = run_month(dataset)
    strict = replace(DEFAULT_CONFIG, batch_max_per_vendor=Decimal("1000"))
    wf11 = BatchWorkflow(r11.batches[0], r11, c11, a11, strict)
    wf11.run_checker_a()
    b_sign = wf11.run_checker_b()
    check(not b_sign.ok and wf11.state == ESTADO_DETENIDO,
          "limite por proveedor de 1000 -> checker B detiene el lote")

    print("== 12. Rechazo humano: devuelve el lote ==")
    r12, a12, c12 = run_month(dataset)
    b12 = r12.batches[0]
    wf12 = BatchWorkflow(b12, r12, c12, a12, DEFAULT_CONFIG)
    wf12.run_checker_a(); wf12.run_checker_b()
    expect_violation(lambda: wf12.reject(APPROVER, ""), "rechazar sin motivo")
    wf12.reject(APPROVER, "Revisar prioridad de pagos con Tesoreria")
    check(wf12.state == ESTADO_RECHAZADO, "lote rechazado")
    check(all(r12.outcomes[i].status == STATUS_LOTE_DEVUELTO for i in b12.invoice_ids),
          "facturas del lote en estado lote_devuelto")
    expect_violation(wf12.release_to_bank, "liberar un lote rechazado")
    reject_events = [ev for ev in a12.events if ev.action == "rechazo-lote"]
    check(len(reject_events) == 1
          and reject_events[0].evidence.get("motivo") == "Revisar prioridad de pagos con Tesoreria",
          "audit trail registra el rechazo con motivo")

    print("== 13. Password gate server-side ==")
    from ap_control_tower.ui.auth import PASSWORD_ENV_VAR, verify_password
    check(verify_password("s3creta", "s3creta"), "password correcta -> acceso")
    check(not verify_password("otra", "s3creta"), "password incorrecta -> denegado")
    check(not verify_password("", "s3creta") and not verify_password("x", ""),
          "vacios -> denegado (sin env var no hay acceso)")
    # En el codigo de la APP (app.py + paquete) la env var solo puede LEERSE;
    # asignarle un literal seria un password en el repo. Mencionar su NOMBRE
    # (docstrings, mensajes) es valido. (Los evals si setean un password
    # descartable para el subproceso de arranque: fuera de alcance.)
    import re
    assign_pat = re.compile(r"""["']?AP_DEMO_PASSWORD["']?\s*\]?\s*=\s*["']""")
    leaked = []
    app_files = [ROOT / "app.py"] + sorted((ROOT / "ap_control_tower").rglob("*.py"))
    for py in app_files:
        for line in py.read_text(encoding="utf-8", errors="ignore").splitlines():
            if assign_pat.search(line):
                leaked.append(f"{py.name}: {line.strip()}")
    check(not leaked, f"ningun password asignado como literal en el codigo de la app {leaked or ''}")

    if "--sin-app" in sys.argv:
        print("== 14. Arranque de la app: SALTEADO (--sin-app) ==")
    else:
        print("== 14. La app arranca sin API keys ni red externa ==")
        check(_boot_app_check(), "streamlit sirve el health endpoint con entorno minimo "
                                 "(solo AP_DEMO_PASSWORD) en un puerto libre por CLI")

    print()
    if failures:
        print(f"EVALS ROJOS: {len(failures)} fallas")
        return 1
    print("EVALS VERDES: todas las verificaciones pasan (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
