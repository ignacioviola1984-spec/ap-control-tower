"""Run the managed PDF PoC against an ignored local folder.

Required environment variables:
    GOOGLE_CLOUD_PROJECT
    DOCUMENT_AI_PROCESSOR_ID
Optional:
    DOCUMENT_AI_LOCATION (defaults to us)

Usage:
    python evals/run_document_ai_poc.py docs/poc-real
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ap_control_tower.extraction.document_ai import (  # noqa: E402
    DocumentAIConfig,
    extract_uploaded_document,
)
from ap_control_tower.extraction.schema import FIELD_ORDER  # noqa: E402


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    return str(value)


def main() -> int:
    if DocumentAIConfig.from_env() is None:
        print("ERROR: faltan GOOGLE_CLOUD_PROJECT y/o DOCUMENT_AI_PROCESSOR_ID")
        return 2

    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "poc-real"
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"ERROR: no se encontraron PDFs en {folder}")
        return 2

    results = [extract_uploaded_document(pdf.name, pdf.read_bytes()) for pdf in pdfs]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "runs" / "document-ai" / stamp
    output.mkdir(parents=True, exist_ok=True)
    report = output / "extracted_documents.csv"
    with report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["archivo", "motor", "confianza", *FIELD_ORDER, "warnings"])
        for result in results:
            writer.writerow([
                result.doc_id,
                result.engine,
                str(result.confidence),
                *[_cell(result.document[field]) for field in FIELD_ORDER],
                " | ".join(result.warnings),
            ])

    invoices = [result for result in results if result.document["document_type"] == "invoice"]
    review = [result.doc_id for result in invoices if result.warnings]
    print(f"OK: {len(results)} documentos; {len(invoices)} facturas; {len(review)} a revisar")
    print(f"Reporte local ignorado por Git: {report}")
    if review:
        print("Revision:", ", ".join(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
