"""Shadow mode: Gemma 4 local vs Google Document AI, campo por campo.

Correr sobre 100-200 facturas reales ANTES de cortar Document AI del flujo
principal. Trata el resultado de Document AI como referencia de paridad (no
como verdad absoluta) y reutiliza el comparador v2, asi que los null cuentan
y las alucinaciones se reportan por separado.

Uso:
    python -m evals.run_gemma_shadow docs/poc-real --out runs/gemma-shadow

Criterio de corte sugerido (mismo umbral que el resto de los evals):
    >= 98% de paridad en numero_factura, fecha_emision, importe_neto,
    importe_total y proveedor_tax_id. Con eso, Document AI pasa de motor
    principal a fallback pago por documento problematico.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ap_control_tower.extraction.comparator import compare_batch  # noqa: E402
from ap_control_tower.extraction.document_ai import (  # noqa: E402
    extract_uploaded_document as document_ai_flow,
    is_document_ai_configured,
)
from ap_control_tower.extraction.gemma import (  # noqa: E402
    GemmaConfig,
    extract_with_gemma,
)
from ap_control_tower.extraction.schema import FIELD_ORDER  # noqa: E402

CAMPOS_CRITICOS = (
    "numero_factura", "fecha_emision", "importe_neto",
    "importe_total", "proveedor_tax_id",
)
UMBRAL_CORTE = 0.98


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="carpeta con PDFs reales")
    parser.add_argument("--out", default="runs/gemma-shadow", help="carpeta de salida")
    args = parser.parse_args()

    config = GemmaConfig.from_env()
    if config is None:
        sys.exit("GEMMA_DISABLED esta definido: no hay motor que evaluar")
    if not is_document_ai_configured():
        sys.exit("Document AI no configurado: no hay referencia contra la que comparar")

    pdfs = sorted(Path(args.input).glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No hay PDFs en {args.input}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs, filas_costo = [], []
    for pdf in pdfs:
        print(f"Procesando {pdf.name} ...")
        data = pdf.read_bytes()
        gemma = extract_with_gemma(pdf.name, data, config)
        managed = document_ai_flow(pdf.name, data)
        pairs.append((pdf.stem, gemma.document, managed.document))
        filas_costo.append({
            "doc_id": pdf.stem,
            "gemma_warnings": len(gemma.warnings),
            "gemma_confidence": str(gemma.confidence),
            "docai_engine": managed.engine,
            "necesitaria_fallback_pago": bool(gemma.warnings),
        })

    report = compare_batch(pairs)

    detalle = out_dir / "shadow_detalle.csv"
    with detalle.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["doc_id", "campo", "resultado", "document_ai", "gemma4"])
        for r in report.results:
            writer.writerow([r.doc_id, r.field, r.outcome, r.golden, r.extracted])

    paridad_por_campo = {}
    for field in FIELD_ORDER:
        del_campo = [r for r in report.results if r.field == field]
        if del_campo:
            ok = sum(1 for r in del_campo if r.outcome == "acierto")
            paridad_por_campo[field] = ok / len(del_campo)

    criticos_ok = all(paridad_por_campo.get(f, 0) >= UMBRAL_CORTE for f in CAMPOS_CRITICOS)
    fallback_rate = sum(1 for f in filas_costo if f["necesitaria_fallback_pago"]) / len(filas_costo)

    resumen = {
        "documentos": len(pdfs),
        "accuracy_global_vs_document_ai": round(report.accuracy(), 4),
        "paridad_por_campo": {k: round(v, 4) for k, v in sorted(paridad_por_campo.items())},
        "campos_criticos": {f: round(paridad_por_campo.get(f, 0), 4) for f in CAMPOS_CRITICOS},
        "tasa_fallback_pago_estimada": round(fallback_rate, 4),
        "veredicto": (
            "LISTO PARA CORTAR: Document AI pasa a fallback"
            if criticos_ok else
            f"NO CORTAR TODAVIA: hay campos criticos bajo {UMBRAL_CORTE:.0%}"
        ),
    }
    (out_dir / "shadow_resumen.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with (out_dir / "shadow_costo.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(filas_costo[0].keys()))
        writer.writeheader()
        writer.writerows(filas_costo)

    print(f"\n{'CAMPO':32} PARIDAD")
    for field, pct in sorted(paridad_por_campo.items()):
        marca = "  <- CRITICO" if field in CAMPOS_CRITICOS else ""
        print(f"{field:32} {pct:7.1%}{marca}")
    print(f"\nParidad global: {report.accuracy():.1%}")
    print(f"Tasa estimada de fallback pago: {fallback_rate:.1%} de los documentos")
    print(f"Veredicto: {resumen['veredicto']}")
    print(f"Salidas: {detalle}, {out_dir / 'shadow_resumen.json'}")
    return 0 if criticos_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
