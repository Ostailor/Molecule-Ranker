from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from molecule_ranker.models.splits import build_model_splits, validate_split_leakage


def test_scaffold_split_keeps_matching_scaffolds_together(tmp_path: Path) -> None:
    result = build_model_splits(
        [
            {"result_id": "r1", "candidate_id": "c1", "canonical_smiles": "Cc1ccccc1"},
            {"result_id": "r2", "candidate_id": "c2", "canonical_smiles": "Oc1ccccc1"},
            {"result_id": "r3", "candidate_id": "c3", "canonical_smiles": "CC1CCCCC1"},
            {"result_id": "r4", "candidate_id": "c4", "canonical_smiles": "CCC1CCCCC1"},
        ],
        strategy="scaffold",
        output_dir=tmp_path,
        config={"test_fraction": 0.5},
    )

    by_candidate = {row["candidate_id"]: row for row in result.assignments}

    assert result.strategy == "scaffold"
    assert by_candidate["c1"]["scaffold"] == by_candidate["c2"]["scaffold"]
    assert by_candidate["c3"]["scaffold"] == by_candidate["c4"]["scaffold"]
    assert by_candidate["c1"]["split"] == by_candidate["c2"]["split"]
    assert by_candidate["c3"]["split"] == by_candidate["c4"]["split"]
    assert by_candidate["c1"]["split"] != by_candidate["c3"]["split"]
    assert result.leakage_check_report["passed"] is True
    assert result.assignment_path is not None
    assert json.loads(result.assignment_path.read_text())["strategy"] == "scaffold"


def test_duplicate_inchikey_leakage_is_caught() -> None:
    report = validate_split_leakage(
        [
            {"result_id": "r1", "inchi_key": "SAME-INCHI", "split": "train"},
            {"result_id": "r2", "inchi_key": "SAME-INCHI", "split": "test"},
        ],
        feature_rows=[],
    )

    assert report["passed"] is False
    assert "inchi_key_overlap" in report["checks"]
    assert report["checks"]["inchi_key_overlap"]["values"] == ["SAME-INCHI"]


def test_time_based_split_uses_oldest_rows_for_training() -> None:
    result = build_model_splits(
        [
            {"result_id": "r1", "candidate_id": "c1", "result_date": date(2026, 1, 1)},
            {"result_id": "r2", "candidate_id": "c2", "result_date": date(2026, 1, 2)},
            {"result_id": "r3", "candidate_id": "c3", "result_date": date(2026, 1, 3)},
            {"result_id": "r4", "candidate_id": "c4", "result_date": date(2026, 1, 4)},
        ],
        strategy="time_based",
        config={"test_fraction": 0.25},
    )

    by_result = {row["result_id"]: row["split"] for row in result.assignments}

    assert by_result == {"r1": "train", "r2": "train", "r3": "train", "r4": "test"}
    assert result.leakage_check_report["checks"]["future_train_result_date"]["passed"] is True


def test_random_split_is_reproducible_with_seed() -> None:
    rows = [{"result_id": f"r{i}", "candidate_id": f"c{i}"} for i in range(10)]

    first = build_model_splits(rows, strategy="random", config={"seed": 17, "test_fraction": 0.3})
    second = build_model_splits(rows, strategy="random", config={"seed": 17, "test_fraction": 0.3})

    assert first.assignments == second.assignments
    assert [row["split"] for row in first.assignments].count("test") == 3


def test_leakage_report_fails_when_label_present_in_features() -> None:
    report = validate_split_leakage(
        [
            {"result_id": "r1", "candidate_id": "c1", "split": "train"},
            {"result_id": "r2", "candidate_id": "c2", "split": "test"},
        ],
        feature_rows=[
            {
                "row_id": "c1",
                "features": {
                    "molecular_weight": 46.07,
                    "label": 1,
                },
            }
        ],
    )

    assert report["passed"] is False
    assert report["checks"]["feature_label_leakage"]["columns"] == ["label"]
