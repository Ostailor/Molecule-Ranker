from __future__ import annotations

import json

import pytest

from molecule_ranker.design.benchmarks import DesignBenchmarkHarness


def _generated(
    generated_id: str,
    smiles: str,
    *,
    generation_method: str = "selfies_mutation",
    novelty_class: str = "novel_analog",
    diversity_cluster: str = "cluster-1",
    scaffold_id: str | None = None,
    readiness_bucket: str = "ready_for_expert_review",
    readiness_score: float = 0.72,
    oracle_score: float = 0.74,
    uncertainty_score: float = 0.35,
    developability_score: float = 0.7,
    critical_alert: bool = False,
) -> dict[str, object]:
    return {
        "generated_id": generated_id,
        "canonical_smiles": smiles,
        "generation_method": generation_method,
        "conditioned_targets": ["MAOB"],
        "validation": {
            "valid_rdkit_mol": True,
            "pains_or_alerts": ["critical_alert"] if critical_alert else [],
            "rejection_reasons": ["critical alert"] if critical_alert else [],
        },
        "novelty": {
            "novelty_class": novelty_class,
            "max_similarity_to_seed": 0.62,
            "max_similarity_to_existing": 0.38,
        },
        "diversity_cluster": diversity_cluster,
        "descriptors": {"molecular_weight": 190.0},
        "developability_assessment": {
            "developability_score": developability_score,
            "triage_recommendation": "high_risk_flags"
            if critical_alert
            else "favorable_hypothesis",
            "metadata": {"risk_level": "critical" if critical_alert else "low"},
        },
        "score_breakdown": {
            "experiment_readiness_score": readiness_score,
            "uncertainty_score": uncertainty_score,
            "final_generation_score": oracle_score,
        },
        "metadata": {
            "scaffold_id": scaffold_id or diversity_cluster,
            "experiment_readiness": {
                "score": readiness_score,
                "label": readiness_bucket,
                "bucket": readiness_bucket,
            },
            "oracle_scoring": {
                "experiment_worthiness_score": oracle_score,
                "risk_flags": ["critical_developability_risk"] if critical_alert else [],
            },
            "uncertainty": {
                "overall_uncertainty": uncertainty_score,
                "active_learning_value": 1.0 - uncertainty_score,
            },
        },
    }


def test_benchmark_works_on_generated_artifact(tmp_path) -> None:
    artifact = {
        "generated_count": 3,
        "retained_count": 2,
        "rejected_count": 1,
        "retained_generated_molecules": [
            _generated("gen-1", "CCOc1ccccc1", scaffold_id="scaffold-a"),
            _generated(
                "gen-2",
                "CCN(CC)CC",
                generation_method="fragment_grower",
                scaffold_id="scaffold-b",
                readiness_bucket="active_learning_candidate",
                readiness_score=0.61,
                oracle_score=0.67,
            ),
        ],
        "rejected_generated_molecules": [
            {
                "generated_molecule": _generated(
                    "gen-3",
                    "CCOc1ccccc1",
                    novelty_class="near_duplicate",
                    critical_alert=True,
                ),
                "rejection_reasons": ["near_duplicate"],
            }
        ],
        "metadata": {"generation_cost": 9.0, "oracle_call_count": 12},
    }

    report = DesignBenchmarkHarness(random_seed=13).benchmark_artifact(
        artifact,
        output_dir=tmp_path,
    )

    assert report.metrics.validity_rate == pytest.approx(1.0)
    assert report.metrics.uniqueness_rate == pytest.approx(1.0)
    assert report.metrics.novelty_rate == pytest.approx(2 / 3, abs=0.001)
    assert report.metrics.scaffold_diversity == pytest.approx(1.0)
    assert report.metrics.critical_alert_rate == pytest.approx(1 / 3, abs=0.001)
    assert report.metrics.generation_cost_per_retained_candidate == pytest.approx(4.5)
    assert report.metrics.experiment_readiness_distribution["buckets"][
        "ready_for_expert_review"
    ] == 1
    assert (tmp_path / "benchmark_report.json").exists()
    assert (tmp_path / "benchmark_report.md").exists()
    saved = json.loads((tmp_path / "benchmark_report.json").read_text())
    assert saved["benchmark_name"] == "internal_design_generation_v1_1"


def test_empty_generation_handled(tmp_path) -> None:
    report = DesignBenchmarkHarness(random_seed=7).benchmark_artifact(
        {
            "generated_count": 0,
            "retained_count": 0,
            "rejected_count": 0,
            "retained_generated_molecules": [],
            "rejected_generated_molecules": [],
        },
        output_dir=tmp_path,
    )

    assert report.metrics.generated_count == 0
    assert report.metrics.validity_rate == 0.0
    assert report.metrics.generator_contribution == {}
    assert report.metrics.oracle_score_distribution["count"] == 0


def test_generator_contribution_computed() -> None:
    report = DesignBenchmarkHarness(random_seed=3).benchmark_artifact(
        {
            "generated_count": 3,
            "retained_generated_molecules": [
                _generated("gen-1", "CCO", generation_method="selfies_mutation"),
                _generated("gen-2", "CCN", generation_method="fragment_grower"),
            ],
            "rejected_generated_molecules": [
                {
                    "generated_molecule": _generated(
                        "gen-3",
                        "CCC",
                        generation_method="fragment_grower",
                    ),
                    "rejection_reasons": ["low_score"],
                }
            ],
        }
    )

    assert report.metrics.generator_contribution["fragment_grower"]["generated_count"] == 2
    assert report.metrics.generator_contribution["fragment_grower"]["retained_count"] == 1
    assert report.metrics.generator_contribution["fragment_grower"]["retention_rate"] == 0.5
    assert report.metrics.generator_contribution["selfies_mutation"]["retention_rate"] == 1.0


def test_pmo_style_budget_counter_works() -> None:
    report = DesignBenchmarkHarness(random_seed=1).benchmark_artifact(
        {
            "generated_count": 2,
            "retained_generated_molecules": [
                _generated("gen-1", "CCO"),
                _generated("gen-2", "CCN"),
            ],
            "rejected_generated_molecules": [],
            "metadata": {"oracle_call_count": 9},
        },
        config={"pmo_oracle_budget": 12, "enable_pmo_tracking": True},
    )

    assert report.optional_modes["pmo"]["enabled"] is True
    assert report.optional_modes["pmo"]["oracle_calls_used"] == 9
    assert report.optional_modes["pmo"]["oracle_budget"] == 12
    assert report.optional_modes["pmo"]["budget_fraction_used"] == pytest.approx(0.75)
