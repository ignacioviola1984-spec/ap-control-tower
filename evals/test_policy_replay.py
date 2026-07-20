"""Regresion hermetica del replay offline run3."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from types import SimpleNamespace

from evals import run_policy_replay


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ap-run3-replay-") as temp:
        root = Path(temp)
        manifest = run_policy_replay.run(SimpleNamespace(
            golden=run_policy_replay.DEFAULT_GOLDEN,
            extraction=run_policy_replay.DEFAULT_EXTRACTION,
            output_dir=root / "runs",
            report_dir=root / "reports",
        ))

        assert manifest["execution"] == {
            "mode": "offline_policy_replay",
            "document_ai_calls": 0,
            "pdfs_opened": 0,
            "cloud_services_called": [],
        }
        assert manifest["scope"]["golden_documents"] == 106
        assert manifest["scope"]["formal_routing_documents"] == 96
        assert len(manifest["scope"]["out_of_scope_extraction_rows"]) == 3

        run2 = manifest["metrics"]["run2_reconstructed"]
        assert (run2["tp"], run2["fp"], run2["fn"], run2["tn"]) == (3, 4, 1, 88)
        assert run2["routing_accuracy_pct"] == 94.8
        assert run2["review_recall_pct"] == 75.0

        run3 = manifest["metrics"]["run3_policy_replay"]
        assert (run3["tp"], run3["fp"], run3["fn"], run3["tn"]) == (4, 3, 0, 89)
        assert run3["routing_accuracy_pct"] == 96.9
        assert run3["review_precision_pct"] == 57.1
        assert run3["review_recall_pct"] == 100.0

        changes = {item["doc_id"]: item for item in manifest["route_changes"]}
        assert set(changes) == {"GD-018", "GD-107", "GD-119"}
        assert changes["GD-107"]["run3"] == "revision_humana"
        assert changes["GD-119"]["run3"] == "revision_humana"
        assert manifest["duplicate_control"]["detected_expected_ids"] == [
            "GD-106", "GD-107", "GD-108"]
        assert manifest["smoke_plan"]["status"] == (
            "deferred_until_zoho_sage_integrations")
        assert len(manifest["smoke_plan"]["entry_conditions"]) == 4
        assert len(manifest["commercial_evidence"]["defensible_claims"]) == 4
        assert len(manifest["commercial_evidence"]["claims_not_supported_yet"]) == 3

        with (root / "runs/run3_policy_replay_detail.csv").open(
                encoding="utf-8-sig", newline="") as handle:
            assert len(list(csv.DictReader(handle))) == 106
        with (root / "runs/run3_cloud_smoke_candidates.csv").open(
                encoding="utf-8-sig", newline="") as handle:
            assert len(list(csv.DictReader(handle))) == 15

    print("PASS run3 policy replay: 106 golden, 0 cloud calls, metrics reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
