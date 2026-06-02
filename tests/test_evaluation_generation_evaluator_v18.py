from __future__ import annotations

import json

import pytest

from molecule_ranker.evaluation.evaluators.generation import evaluate_generation


def _metric(report, name: str):
    for metric in report.metrics:
        if metric.name == name:
            return metric
    raise AssertionError(f"missing metric {name}")


def _generated_artifact() -> dict[str, object]:
    return {
        "artifact_id": "generated_candidates.json",
        "retained_generated_molecules": [
            {
                "generated_id": "G1",
                "canonical_smiles": "CCN",
                "valid": True,
                "is_novel": True,
                "scaffold": "scaffold-a",
                "developability_pass": True,
                "critical_alerts": [],
                "structure_qc_status": "pass",
                "experiment_readiness": "ready",
                "medchem_decision": "retain",
                "sampled_for_experiment": True,
                "docking_score": 99.0,
                "predicted_probability": 0.99,
            },
            {
                "generated_id": "G2",
                "canonical_smiles": "CCO",
                "valid": True,
                "is_novel": False,
                "scaffold": "scaffold-a",
                "developability_pass": False,
                "critical_alerts": ["reactive_group"],
                "structure_qc_status": "fail",
                "experiment_readiness": "defer",
                "medchem_decision": "deprioritize",
                "sampled_for_experiment": True,
                "docking_score": 120.0,
                "model_prediction_hit": True,
            },
            {
                "generated_id": "G3",
                "canonical_smiles": "CCN",
                "valid": False,
                "is_novel": True,
                "scaffold": "scaffold-b",
                "developability_score": 0.8,
                "critical_alert": False,
                "pose_qc": "pass",
                "experiment_readiness": "review",
                "medchem_decision": "reject",
                "sampled_for_experiment": False,
            },
        ],
    }


def _labels() -> dict[str, object]:
    return {
        "artifact_id": "assay_results.json",
        "assay_results": [
            {
                "generated_id": "G1",
                "canonical_smiles": "CCN",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "result-g1",
            },
            {
                "generated_id": "G2",
                "canonical_smiles": "CCC",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "result-g2-wrong-structure",
            },
        ],
    }


def test_generation_evaluator_scores_generation_quality_and_exact_hits() -> None:
    report = evaluate_generation(
        generated_molecule_artifacts={"generated_candidates": _generated_artifact()},
        imported_outcome_labels={"imported_assay_results": _labels()},
        evaluation_id="generation-eval",
    )

    assert _metric(report, "validity_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "uniqueness_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "novelty_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "scaffold_diversity").value == pytest.approx(2 / 3)
    assert _metric(report, "developability_pass_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "critical_alert_rate").value == pytest.approx(1 / 3)
    assert _metric(report, "structure_qc_pass_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "exact_experimental_hit_rate").value == pytest.approx(1 / 3)
    assert _metric(report, "active_learning_sample_efficiency").value == pytest.approx(1 / 2)

    readiness = json.loads(str(_metric(report, "experiment_readiness_distribution").value))
    assert readiness == {"defer": 1, "ready": 1, "review": 1}
    medchem = json.loads(str(_metric(report, "medchem_critique_distribution").value))
    assert medchem == {"deprioritize": 1, "reject": 1, "retain": 1}
    assert report.metadata["label_rules"]["docking_scores_are_hits"] is False
    assert report.metadata["exact_hit_count"] == 1


def test_generation_evaluator_does_not_count_docking_or_model_predictions_as_hits() -> None:
    report = evaluate_generation(
        generated_molecule_artifacts={"generated_candidates": _generated_artifact()},
        imported_outcome_labels={
            "model_predictions": {
                "artifact_id": "model_predictions.json",
                "model_predictions": [
                    {"generated_id": "G1", "outcome_label": "positive", "model_version": "m1"}
                ],
            }
        },
    )

    assert _metric(report, "exact_experimental_hit_rate").value == 0.0
    assert _metric(report, "active_learning_sample_efficiency").value == 0.0
    assert "no_exact_experimental_hits" in report.warnings


def test_generation_evaluator_requires_exact_generated_structure_for_validation() -> None:
    labels = {
        "artifact_id": "assay_results.json",
        "assay_results": [
            {
                "generated_id": "G1",
                "canonical_smiles": "CCO",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "wrong-structure",
            }
        ],
    }

    report = evaluate_generation(
        generated_molecule_artifacts={"generated_candidates": _generated_artifact()},
        imported_outcome_labels={"imported_assay_results": labels},
    )

    assert _metric(report, "exact_experimental_hit_rate").value == 0.0
    assert "generated_result_without_exact_structure_match" in report.warnings
