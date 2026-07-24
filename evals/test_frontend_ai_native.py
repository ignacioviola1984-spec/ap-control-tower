"""Regresión del frontend AI-native: sistema visual, command center, bandeja
y workspace de revisión. Exit 0 = verde."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    from ap_control_tower.ui import design
    from ap_control_tower.ui import command_center as cc
    from ap_control_tower.ui.pilot_pages_documents import (
        QUICK_VIEWS,
        apply_quick_view,
    )

    print("== Sistema visual (design) ==")
    # Índigo RESERVADO a la IA: ningún tono operativo puede pisarlo.
    operativos = {design.OK, design.WARN, design.RISK, design.INFO, design.BRAND}
    check(design.AI not in operativos,
          "el índigo de IA no coincide con ningún color operativo")
    check(design.money(1234567.89, "EUR") == "EUR 1.234.567,89",
          "formato monetario europeo con separador de miles")
    check(design.money("no-numero") == "—", "importe no numérico degrada a guion")
    check(design.priority_tone(["la cuenta de cobro no coincide"])[1] == "risk",
          "desvío de pago se prioriza como crítico")
    check(design.priority_tone(["posible factura duplicada"])[1] == "warn",
          "duplicado se prioriza como alto")
    check(design.priority_tone([])[0] == "Normal",
          "sin motivos, prioridad normal")
    # El escape evita inyección desde datos del documento.
    check("&lt;script&gt;" in design.chip("<script>", "risk"),
          "el chip escapa el marcado del contenido")

    print("== Command center: briefing determinista ==")

    class _Doc(dict):
        pass

    class _Result:
        def __init__(self, doc_id, document, warnings=None):
            self.doc_id = doc_id
            self.document = document
            self.warnings = warnings or []
            self.field_confidences = {}
            self.engine = "fallback_local"

    hoy = date.today()

    def factura(doc_id, total, venc_offset, extra=None):
        doc = {
            "document_type": "invoice", "moneda": "EUR",
            "proveedor_nombre_comercial": f"Prov {doc_id}",
            "importe_total": str(total),
            "fecha_vencimiento_calculada": (hoy + timedelta(days=venc_offset)).isoformat(),
            "cliente_nombre": "BRAND UP SLU", "cliente_tax_id": "B85902583",
        }
        if extra:
            doc.update(extra)
        return _Result(doc_id, doc)

    class _Session:
        def __init__(self, results):
            self.results = results
            self.review_decisions = {}
            self.approval_decisions = {}

    session = _Session([
        factura("A", 22264, 3),                                   # vence pronto
        factura("B", 105.98, 5, {"saldo_pendiente": "0.00"}),     # ya pagada -> riesgo
        factura("C", 1185.80, -2),                                # vencida
    ])
    signals = cc.collect_signals(session, today=hoy)
    check(signals["total_vence_pronto"].get("EUR", 0) > 0,
          "detecta importes que vencen en 7 días")
    check(signals["total_vencidas"].get("EUR", 0) > 0,
          "detecta importes ya vencidos")
    criticos = [r for r in signals["riesgos"] if r["tono"] == "risk"]
    check(any("ya pagada" in " ".join(r["motivos"]).casefold() for r in criticos),
          "marca la factura ya pagada como riesgo crítico")
    texto = cc.briefing_text(
        {"received": 3, "eligible": 0, "approved": 0, "errors": 0}, signals)
    check("<b>" in texto and "vencen" in texto,
          "el briefing compone una frase con montos reales")
    # El briefing no debe romperse con una sesión vacía.
    vacio = cc.briefing_text(
        {"received": 0, "eligible": 0, "approved": 0, "errors": 0},
        cc.collect_signals(_Session([]), today=hoy))
    check("No hay documentos" in vacio, "briefing vacío es seguro")

    print("== Bandeja: vistas rápidas ==")
    rows = [
        {"state_code": "pending_review", "reasons": ["x"], "Estado": "Pendiente",
         "_importe_raw": "9000", "_vencimiento_raw": (hoy + timedelta(days=2)).isoformat()},
        {"state_code": "processed", "reasons": [], "Estado": "OK",
         "_importe_raw": "50", "_vencimiento_raw": (hoy + timedelta(days=40)).isoformat()},
    ]
    check(len(apply_quick_view(rows, "Para revisar")) == 1,
          "«Para revisar» filtra por estado pendiente")
    check(len(apply_quick_view(rows, "Con anomalías")) == 1,
          "«Con anomalías» filtra por motivos")
    check(len(apply_quick_view(rows, "Vence esta semana")) == 1,
          "«Vence esta semana» usa el vencimiento crudo")
    check(set(QUICK_VIEWS) >= {"Todos", "Para revisar", "Vence esta semana",
                               "Alto importe", "Con anomalías"},
          "están las cinco vistas rápidas de la especificación")

    print("== Regla de CSS: sin selectores internos inestables ==")
    import re

    css_files = [
        ROOT / "ap_control_tower" / "ui" / "design.py",
        ROOT / "ap_control_tower" / "ui" / "pilot_shell.py",
        ROOT / "ap_control_tower" / "ui" / "review_workspace.py",
        ROOT / "ap_control_tower" / "ui" / "agent_panel.py",
    ]
    # Solo se inspecciona lo que hay DENTRO de los bloques <style>: la prosa de
    # los docstrings puede nombrar 'emotion-cache' al explicar por qué NO se usa.
    bloques = []
    for path in css_files:
        bloques += re.findall(r"<style>(.*?)</style>",
                              path.read_text(encoding="utf-8"), re.S)
    css = "\n".join(bloques)
    check("emotion-cache" not in css,
          "el CSS no cuelga de clases internas emotion-cache")

    if failures:
        print(f"\nFRONTEND ROJO: {len(failures)} fallo(s)")
        return 1
    print("\nFRONTEND VERDE: sistema visual, briefing, vistas y CSS estable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
