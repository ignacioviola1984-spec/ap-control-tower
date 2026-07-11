"""Evals de la API interna (Fase 4). exit 0 = verde.

Usa el TestClient de FastAPI (sin red). SKIP con exit 0 si FastAPI no esta
instalado (dependencia opcional; la demo no la necesita). Cubre: OpenAPI,
creacion idempotente de corrida, paginacion, enmascaramiento bancario, gate
(aprobar/cerrar/rechazar), idempotencia, validacion, errores, correlacion y
auditoria.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient
    except Exception:
        print("== API: SALTEADO (FastAPI no instalado) ==")
        print("  SKIP  instalar con: pip install -r requirements-api.txt")
        return 0

    from ap_control_tower.api.main import create_app

    client = TestClient(create_app())

    print("== 1. Infra: health + OpenAPI + correlacion ==")
    r = client.get("/healthz")
    check(r.status_code == 200 and r.json()["status"] == "ok", "healthz responde ok")
    check("X-Correlation-ID" in r.headers, "toda respuesta lleva X-Correlation-ID")
    given = client.get("/healthz", headers={"X-Correlation-ID": "cid-test-123"})
    check(given.headers.get("X-Correlation-ID") == "cid-test-123",
          "el correlation_id provisto por el cliente se respeta")
    schema = client.get("/openapi.json")
    check(schema.status_code == 200 and "/v1/runs" in schema.json()["paths"],
          "OpenAPI disponible con las rutas v1 (documentacion automatica)")

    print("== 2. Crear corrida (idempotente por run_id) ==")
    r = client.post("/v1/runs", json={"run_id": "run-api-test"})
    check(r.status_code == 201, "POST /v1/runs -> 201")
    summary = r.json()
    check(summary["documentos"] == 42 and summary["bloqueadas"] == 6,
          f"resumen: {summary['documentos']} docs, {summary['bloqueadas']} bloqueadas")
    check(len(summary["lotes"]) == 4, "4 lotes de pago")
    again = client.post("/v1/runs", json={"run_id": "run-api-test"})
    check(again.json()["run_id"] == summary["run_id"],
          "crear con el mismo run_id devuelve la misma corrida (idempotente)")
    rid = summary["run_id"]

    print("== 3. Documentos: paginacion + enmascaramiento ==")
    r = client.get(f"/v1/runs/{rid}/documents", params={"page": 1, "size": 10})
    body = r.json()
    check(body["total"] == 42 and len(body["items"]) == 10 and body["page"] == 1,
          f"paginacion: total {body['total']}, pagina de {len(body['items'])}")
    r = client.get(f"/v1/runs/{rid}/documents/INV-024")
    d = r.json()
    check(d["estado"] == "bloqueada" and d["control_bloqueante"] == "C6_DATOS_BANCARIOS"
          and d["fase_ciclo_vida"] == "bloqueado",
          "INV-024: bloqueada por C6, fase 'bloqueado'")
    check(d["iban_enmascarado"] and "*" in d["iban_enmascarado"],
          f"IBAN enmascarado en la respuesta ({d['iban_enmascarado']})")

    print("== 4. Excepciones y metricas ==")
    r = client.get(f"/v1/runs/{rid}/exceptions")
    exc = r.json()["items"]
    fraude = [e for e in exc if e["alerta_fraude"]]
    check(any(e["invoice_id"] == "INV-024" for e in fraude),
          "INV-024 aparece como excepcion con alerta de fraude")
    r = client.get(f"/v1/runs/{rid}/metrics")
    m = r.json()
    check(m["documentos"] == 42 and 0 <= m["tasa_bloqueo"] <= 1,
          f"metricas: tasa_bloqueo={m['tasa_bloqueo']}, revision={m['tasa_revision_humana']}")

    print("== 5. Validacion estricta de entradas ==")
    bad = client.post(f"/v1/runs/{rid}/batches/2026-06-04/approve", json={"aprobador": ""})
    check(bad.status_code == 422, "aprobar con aprobador vacio -> 422 (validacion)")

    print("== 6. Gate: aprobar+liberar y cerrar (idempotente) ==")
    iso = summary["lotes"][0]["fecha_lote"]
    r = client.post(f"/v1/runs/{rid}/batches/{iso}/approve",
                    json={"aprobador": "Apoderada Demo"})
    check(r.status_code == 200 and r.json()["estado"] == "liberado_al_banco",
          f"lote {iso} aprobado y liberado")
    r2 = client.post(f"/v1/runs/{rid}/batches/{iso}/approve",
                     json={"aprobador": "Apoderada Demo"})
    check(r2.status_code == 200 and r2.json()["estado"] == "liberado_al_banco",
          "re-aprobar un lote ya liberado es idempotente (200, sin error)")
    r = client.post(f"/v1/runs/{rid}/batches/{iso}/close")
    check(r.status_code == 200 and r.json()["estado"] == "cerrado",
          f"lote {iso} cerrado (conciliacion)")

    print("== 7. El gate no se puede saltar (409) ==")
    fresh = client.post("/v1/runs", json={"run_id": "run-api-reject"}).json()
    fid, fiso = fresh["run_id"], fresh["lotes"][0]["fecha_lote"]
    client.post(f"/v1/runs/{fid}/batches/{fiso}/reject",
                json={"aprobador": "X", "motivo": "revisar"})
    r = client.post(f"/v1/runs/{fid}/batches/{fiso}/approve", json={"aprobador": "X"})
    check(r.status_code == 409 and r.json()["error"] == "gate_violation",
          "aprobar un lote rechazado -> 409 gate_violation")
    check(r.json().get("correlation_id"), "el error incluye correlation_id")

    print("== 8. Revision humana por la API ==")
    r = client.post(f"/v1/runs/{fid}/documents/INV-014/review",
                    json={"tipo": "datos_internos", "confirmado_por": "Revisora",
                          "cost_center": "CO-020", "internal_approver": "Mkt / J. Peralta",
                          "contract_ref": "EMAIL-2026-05"})
    check(r.status_code == 200 and r.json()["estado"] == "en_lote",
          "confirmar datos internos de INV-014 -> en_lote")

    print("== 9. Auditoria paginada con cadena verificada ==")
    r = client.get(f"/v1/runs/{rid}/audit", params={"size": 5})
    a = r.json()
    check(a["cadena_verificada"] is True and a["total"] > 0 and len(a["items"]) == 5,
          f"audit: {a['total']} eventos, cadena verificada, pagina de 5")

    print("== 10. 404 en recursos inexistentes ==")
    check(client.get("/v1/runs/no-existe").status_code == 404, "corrida inexistente -> 404")
    check(client.get(f"/v1/runs/{rid}/documents/INV-999").status_code == 404,
          "documento inexistente -> 404")

    print("== 11. Carga y procesamiento de documento real (extraccion) ==")
    try:
        from io import BytesIO

        from reportlab.pdfgen import canvas
        buf = BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, "FACTURA Nro F-2026/999")
        c.drawString(100, 730, "Proveedor Demo SL - CIF ESB12345678")
        c.drawString(100, 710, "Total: 1210,00 EUR")
        c.save()
        pdf_bytes = buf.getvalue()
        r = client.post("/v1/documents",
                        files={"file": ("factura.pdf", pdf_bytes, "application/pdf")})
        check(r.status_code == 200 and "document" in r.json(),
              f"upload procesa el PDF (motor: {r.json().get('motor')})")
        doc = r.json()["document"]
        check(doc.get("iban") is None or "*" in str(doc.get("iban")),
              "el IBAN (si aparece) viaja enmascarado en la respuesta de upload")
    except Exception as exc:  # pragma: no cover
        check(False, f"upload de PDF fallo: {exc}")

    print()
    if failures:
        print(f"API ROJA: {len(failures)} fallas")
        return 1
    print("API VERDE: endpoints, idempotencia, paginacion y enmascaramiento OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
