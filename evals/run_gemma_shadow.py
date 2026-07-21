"""Shadow mode: motores locales (via Ollama) vs Google Document AI, campo por campo.

Correr sobre facturas reales ANTES de cortar Document AI del flujo principal.
Trata el resultado de Document AI como referencia de paridad (no como verdad
absoluta) y reutiliza el comparador v2, asi que los null cuentan y las
alucinaciones se reportan por separado de las omisiones.

Uso:
    # primera corrida: llama a Document AI y cachea la referencia
    python -m evals.run_gemma_shadow docs/poc-real --out runs/gemma-shadow \\
        --referencia ../ref/docai.json --modelo gemma3:4b

    # corridas siguientes: reusa la referencia cacheada, sin llamadas pagas
    python -m evals.run_gemma_shadow docs/poc-real --out runs/gemma-shadow \\
        --referencia ../ref/docai.json --modelo qwen2.5vl:3b

La referencia cacheada permite medir varios motores locales contra exactamente
la misma referencia paga, pagando Document AI una sola vez. El archivo de
referencia contiene datos de facturas reales: guardarlo FUERA del repo.

Criterio de corte sugerido (mismo umbral que el resto de los evals):
    >= 98% de paridad en numero_factura, fecha_emision, importe_neto,
    importe_total y proveedor_tax_id. Con eso, Document AI pasa de motor
    principal a fallback pago por documento problematico.

OJO con el tamano de muestra: el umbral de 98% sobre los 5 campos criticos
necesita del orden de 100-200 facturas para ser significativo. Con n chico el
resultado es evidencia direccional, no el corte formal; el script lo avisa.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
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
from ap_control_tower.extraction.pdf_poc import read_pdf_bytes  # noqa: E402
from ap_control_tower.extraction.schema import FIELD_KINDS, FIELD_ORDER  # noqa: E402

CAMPOS_CRITICOS = (
    "numero_factura", "fecha_emision", "importe_neto",
    "importe_total", "proveedor_tax_id",
)
UMBRAL_CORTE = 0.98
MUESTRA_MINIMA = 100  # por debajo de esto el umbral no es significativo

_SEPS = re.compile(r"[\s\-./,]")


# ------------------------------------------------------- referencia cacheada
MOTOR_REFERENCIA = "google_document_ai_invoice_parser"


def cargar_referencia(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {doc_id: entry["documento"] for doc_id, entry in data.items()}


def motores_de_referencia(path: Path) -> dict[str, str]:
    """{doc_id: engine} de la referencia cacheada.

    No todo lo que hay en la carpeta llega a Document AI: una proforma o una
    orden de compra la frena el clasificador local, y una llamada puede caer
    con error. Esos documentos tienen como referencia el extractor local, no el
    parser pago, asi que medir 'paridad vs Document AI' contra ellos seria
    mentir: se excluyen del calculo y se informan aparte.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {doc_id: entry.get("engine", "?") for doc_id, entry in data.items()}


def construir_referencia(pdfs: list[Path], path: Path) -> dict[str, dict]:
    """Llama a Document AI UNA vez por documento y cachea el resultado."""
    if not is_document_ai_configured():
        sys.exit("Document AI no configurado: no hay referencia contra la que comparar")
    cache: dict[str, dict] = {}
    if path.exists():
        cache = json.loads(path.read_text(encoding="utf-8"))
    for pdf in pdfs:
        if pdf.stem in cache:
            print(f"  referencia cacheada: {pdf.name}")
            continue
        print(f"  Document AI (llamada paga): {pdf.name}")
        result = document_ai_flow(pdf.name, pdf.read_bytes())
        cache[pdf.stem] = {
            "archivo": pdf.name,
            "engine": result.engine,
            "confianza": str(result.confidence),
            "warnings": result.warnings,
            "documento": result.document,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    return {doc_id: entry["documento"] for doc_id, entry in cache.items()}


# --------------------------------------------- alucinacion contra el texto
def _aplanar(value: str) -> str:
    return _SEPS.sub("", str(value)).upper()


def no_verificable_en_texto(doc: dict, texto: str, campos: tuple[str, ...]) -> list[str]:
    """Campos cuyo valor NO aparece literalmente en el texto vectorial del PDF.

    Senal fuerte de invencion, independiente de la referencia: si el motor
    devuelve un CIF que no esta en el documento, lo invento. Solo se aplica a
    campos textuales/id; los importes se reformatean legitimamente y las fechas
    se normalizan a ISO, asi que quedan fuera.
    """
    plano = _aplanar(texto)
    inventados = []
    for campo in campos:
        if FIELD_KINDS.get(campo) not in ("id", "str"):
            continue
        valor = doc.get(campo)
        if valor in (None, ""):
            continue
        if _aplanar(valor) not in plano:
            inventados.append(campo)
    return inventados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="carpeta con PDFs reales")
    parser.add_argument("--out", default="runs/gemma-shadow", help="carpeta de salida")
    parser.add_argument(
        "--referencia",
        help="JSON con la referencia de Document AI. Si no existe se construye "
             "(llamadas pagas) y se cachea; si existe se reusa sin pagar. "
             "Guardarlo FUERA del repo: contiene datos reales.")
    parser.add_argument(
        "--modelo",
        help="modelo de Ollama a evaluar (pisa GEMMA_MODEL para esta corrida)")
    args = parser.parse_args()

    config = GemmaConfig.from_env()
    if config is None:
        sys.exit("GEMMA_DISABLED esta definido: no hay motor que evaluar")
    if args.modelo:
        config = replace_modelo(config, args.modelo)

    pdfs = sorted(Path(args.input).glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No hay PDFs en {args.input}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    excluidos: list[tuple[str, str]] = []
    if args.referencia:
        ref_path = Path(args.referencia)
        referencia = cargar_referencia(ref_path)
        faltantes = [p for p in pdfs if p.stem not in referencia]
        if faltantes:
            print(f"Construyendo referencia de Document AI para {len(faltantes)} documento(s)...")
            referencia = construir_referencia(pdfs, ref_path)
        else:
            print(f"Referencia cacheada completa ({len(referencia)} documentos): sin llamadas pagas.")
        engines = motores_de_referencia(ref_path)
        for doc_id, engine in sorted(engines.items()):
            if engine != MOTOR_REFERENCIA:
                excluidos.append((doc_id, engine))
                referencia.pop(doc_id, None)
        if excluidos:
            print(f"\nExcluidos de la paridad ({len(excluidos)}): su referencia NO es Document AI")
            for doc_id, engine in excluidos:
                print(f"  - {doc_id}  (engine de referencia: {engine})")
            print()
    else:
        if not is_document_ai_configured():
            sys.exit("Document AI no configurado: no hay referencia contra la que comparar")
        referencia = {}

    pairs, filas_costo = [], []
    for pdf in pdfs:
        print(f"Procesando {pdf.name} con {config.model} ...")
        data = pdf.read_bytes()
        inicio = time.time()
        local = extract_with_gemma(pdf.name, data, config)
        segundos = round(time.time() - inicio, 1)

        if referencia:
            golden = referencia.get(pdf.stem)
            if golden is None:
                print(f"  sin referencia para {pdf.stem}, se saltea")
                continue
        else:
            golden = document_ai_flow(pdf.name, data).document

        texto = read_pdf_bytes(pdf.name, data).text
        inventados = no_verificable_en_texto(local.document, texto, CAMPOS_CRITICOS)

        pairs.append((pdf.stem, local.document, golden))
        filas_costo.append({
            "doc_id": pdf.stem,
            "modelo": config.model,
            "segundos": segundos,
            "warnings": len(local.warnings),
            "confianza": str(local.confidence),
            "campos_no_verificables_en_texto": ";".join(inventados),
            "necesitaria_fallback_pago": bool(local.warnings),
        })

    if not pairs:
        sys.exit("No se pudo comparar ningun documento")

    report = compare_batch(pairs)

    detalle = out_dir / "shadow_detalle.csv"
    with detalle.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["doc_id", "campo", "resultado", "document_ai", config.model])
        for r in report.results:
            writer.writerow([r.doc_id, r.field, r.outcome, r.golden, r.extracted])

    paridad_por_campo, desglose_por_campo = {}, {}
    for field in FIELD_ORDER:
        del_campo = [r for r in report.results if r.field == field]
        if not del_campo:
            continue
        ok = sum(1 for r in del_campo if r.outcome == "acierto")
        paridad_por_campo[field] = ok / len(del_campo)
        desglose_por_campo[field] = {
            outcome: sum(1 for r in del_campo if r.outcome == outcome)
            for outcome in ("acierto", "discrepancia", "omision", "alucinacion")
        }

    criticos_ok = all(paridad_por_campo.get(f, 0) >= UMBRAL_CORTE for f in CAMPOS_CRITICOS)
    fallback_rate = sum(1 for f in filas_costo if f["necesitaria_fallback_pago"]) / len(filas_costo)
    segundos = [f["segundos"] for f in filas_costo]
    muestra_suficiente = len(pairs) >= MUESTRA_MINIMA

    if muestra_suficiente:
        veredicto = ("LISTO PARA CORTAR: Document AI pasa a fallback" if criticos_ok
                     else f"NO CORTAR TODAVIA: hay campos criticos bajo {UMBRAL_CORTE:.0%}")
    else:
        veredicto = (
            f"EVIDENCIA DIRECCIONAL, NO CORTE FORMAL: n={len(pairs)} documentos. "
            f"El umbral de {UMBRAL_CORTE:.0%} sobre los campos criticos necesita "
            f"del orden de {MUESTRA_MINIMA}-200 facturas para ser significativo."
        )

    resumen = {
        "modelo_local": config.model,
        "documentos": len(pairs),
        "muestra_significativa_para_el_umbral": muestra_suficiente,
        "accuracy_global_vs_document_ai": round(report.accuracy, 4),
        "totales": report.summary(),
        "paridad_por_campo": {k: round(v, 4) for k, v in sorted(paridad_por_campo.items())},
        "desglose_por_campo": desglose_por_campo,
        "campos_criticos": {f: round(paridad_por_campo.get(f, 0), 4) for f in CAMPOS_CRITICOS},
        "segundos_por_factura": {
            "min": min(segundos), "max": max(segundos),
            "promedio": round(sum(segundos) / len(segundos), 1),
        },
        "documentos_con_campos_criticos_no_verificables_en_texto": sum(
            1 for f in filas_costo if f["campos_no_verificables_en_texto"]),
        "excluidos_referencia_no_document_ai": [
            {"doc_id": d, "engine_referencia": e} for d, e in excluidos],
        "tasa_fallback_pago_estimada": round(fallback_rate, 4),
        "veredicto": veredicto,
    }
    nombre = re.sub(r"[^a-z0-9]+", "-", config.model.lower()).strip("-")
    (out_dir / f"shadow_resumen_{nombre}.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "shadow_resumen.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out_dir / "shadow_costo.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(filas_costo[0].keys()))
        writer.writeheader()
        writer.writerows(filas_costo)

    print(f"\nMotor local: {config.model}   documentos: {len(pairs)}")
    print(f"{'CAMPO':32} {'PARIDAD':>8}  {'ACI':>4} {'DISC':>4} {'OMI':>4} {'ALUC':>4}")
    for field, pct in sorted(paridad_por_campo.items()):
        d = desglose_por_campo[field]
        marca = "  <- CRITICO" if field in CAMPOS_CRITICOS else ""
        print(f"{field:32} {pct:7.1%}  {d['acierto']:4} {d['discrepancia']:4} "
              f"{d['omision']:4} {d['alucinacion']:4}{marca}")
    print(f"\nParidad global: {report.accuracy:.1%}")
    print(f"Totales: {report.summary()}")
    print(f"Segundos por factura: {resumen['segundos_por_factura']}")
    print(f"Docs con campos criticos que NO estan en el texto del PDF: "
          f"{resumen['documentos_con_campos_criticos_no_verificables_en_texto']}/{len(pairs)}")
    print(f"Tasa estimada de fallback pago: {fallback_rate:.1%} de los documentos")
    print(f"Veredicto: {veredicto}")
    print(f"Salidas: {detalle}, {out_dir / 'shadow_resumen.json'}")
    return 0 if (criticos_ok and muestra_suficiente) else 1


def replace_modelo(config: GemmaConfig, modelo: str) -> GemmaConfig:
    """GemmaConfig es frozen: se reemplaza solo el modelo."""
    from dataclasses import replace
    return replace(config, model=modelo)


if __name__ == "__main__":
    raise SystemExit(main())
