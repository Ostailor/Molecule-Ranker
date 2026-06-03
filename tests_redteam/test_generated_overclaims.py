from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.validation.guardrail_audit import run_guardrail_audit


def test_generated_molecule_called_active_fails_guardrail_audit(tmp_path: Path) -> None:
    (tmp_path / "generated_candidates.json").write_text(
        json.dumps(
            {
                "generated": [
                    {
                        "generated_id": "G-REDTEAM-1",
                        "origin": "generated",
                        "label": "confirmed active",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = run_guardrail_audit(tmp_path)

    assert report.status == "fail"
    assert {finding.check_id for finding in report.findings} >= {
        "no_generated_molecule_called_active"
    }


def test_docking_score_and_model_prediction_overclaims_fail_audit(tmp_path: Path) -> None:
    (tmp_path / "structure_report.md").write_text(
        "The docking score is proof of binding for the synthetic candidate.\n",
        encoding="utf-8",
    )
    (tmp_path / "model_prediction_report.md").write_text(
        "The model prediction is evidence for the synthetic candidate.\n",
        encoding="utf-8",
    )

    report = run_guardrail_audit(tmp_path)
    check_ids = {finding.check_id for finding in report.findings}

    assert "no_docking_score_as_binding_proof" in check_ids
    assert "no_model_prediction_as_evidence" in check_ids
