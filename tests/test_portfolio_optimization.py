from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    NoveltyAssessment,
)
from molecule_ranker.portfolio import (
    Program,
    ResourceBudget,
    optimize_portfolio,
)
from molecule_ranker.portfolio.candidate_builder import (
    build_portfolio_candidates_from_artifacts,
)
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    ScoreBreakdown,
)


def _existing(
    name: str,
    *,
    target: str,
    score: float,
    series: str,
    risk_warning: str | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL-{name}"},
        known_targets=[target],
        mechanism_of_action=f"{target} modulation",
        chemical_metadata={"chemical_series": series, "canonical_smiles": f"CC{name}"},
        score=score,
        score_breakdown=ScoreBreakdown(
            disease_target_relevance=score,
            molecule_target_evidence=score,
            mechanism_plausibility=score,
            clinical_precedence=0.3,
            safety_prior=0.5,
            data_quality=0.7,
            novelty_or_repurposing_value=0.6,
            developability_score=0.75,
            final_score=score,
            confidence=0.62,
            explanation="Transparent prioritization score.",
        ),
        developability_assessment=DevelopabilityAssessment(
            molecule_name=name,
            origin="existing",
            structure_available=True,
            canonical_smiles=f"CC{name}",
            developability_score=0.75,
            triage_recommendation="favorable_hypothesis",
        ),
        warnings=[risk_warning] if risk_warning else [],
    )


def _breakdown(final_score: float, uncertainty: float) -> GeneratedMoleculeScoreBreakdown:
    return GeneratedMoleculeScoreBreakdown(
        target_conditioning_score=final_score,
        seed_evidence_score=0.6,
        novelty_score=0.78,
        diversity_score=0.82,
        chemical_validity_score=1.0,
        property_profile_score=0.72,
        literature_context_score=0.5,
        developability_score=0.68,
        objective_alignment_score=0.74,
        generator_ensemble_score=0.7,
        uncertainty_score=uncertainty,
        medchem_critique_score=0.65,
        experiment_readiness_score=0.7,
        active_learning_priority_score=uncertainty,
        final_generation_score=final_score,
        confidence=0.42,
        explanation="Generated hypothesis score only.",
    )


def _generated(
    generated_id: str,
    *,
    target: str,
    score: float,
    series: str,
    uncertainty: float,
    warning: str | None = None,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles=f"CCO{generated_id}",
        canonical_smiles=f"CCO{generated_id}",
        generation_method="generator_ensemble",
        parent_seed_ids=["seed-1"],
        conditioned_targets=[target],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 240.0, "logp": 2.2},
        fingerprints={},
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
            rejection_reasons=[],
        ),
        novelty=NoveltyAssessment(
            duplicate_of_existing=False,
            duplicate_of_generated=False,
            max_similarity_to_existing=0.4,
            max_similarity_to_seed=0.61,
            novelty_class="novel_analog",
        ),
        diversity_cluster=series,
        generation_score=score,
        score_breakdown=_breakdown(score, uncertainty),
        developability_assessment=DevelopabilityAssessment(
            molecule_name=generated_id,
            origin="generated",
            structure_available=True,
            canonical_smiles=f"CCO{generated_id}",
            developability_score=0.68,
            triage_recommendation="review_flags",
        ),
        warnings=[warning] if warning else [],
    )


def _assay_result(result_id: str, generated_id: str, smiles: str) -> AssayResult:
    return AssayResult(
        result_id=result_id,
        candidate_id=generated_id,
        candidate_name=generated_id,
        candidate_origin="generated",
        canonical_smiles=smiles,
        target_symbol="T1",
        assay_context=AssayContext(
            assay_context_id=f"context-{result_id}",
            assay_name="imported endpoint",
            assay_type="biochemical",
            target_symbol="T1",
            endpoint=AssayEndpoint(
                endpoint_id="endpoint-1",
                name="target_engagement",
                endpoint_category="target_engagement",
                directionality="binary",
            ),
        ),
        outcome_label="inconclusive",
        activity_direction="ambiguous",
        confidence=0.7,
        qc_status="passed",
        source="csv_import",
        imported_at=datetime.now(UTC),
    )


def test_portfolio_optimizer_balances_selection_and_surfaces_decision_analytics() -> None:
    generated = _generated("gen-1", target="T2", score=0.74, series="series-b", uncertainty=0.8)
    run = optimize_portfolio(
        program=Program(
            program_id="program-1",
            name="Program 1",
            disease_focus=["Disease A"],
            target_focus=["T1", "T2", "T3"],
        ),
        existing_candidates=[
            _existing("A", target="T1", score=0.82, series="series-a"),
            _existing("B", target="T1", score=0.7, series="series-a"),
            _existing("C", target="T3", score=0.55, series="series-c"),
        ],
        generated_molecules=[
            generated,
            _generated(
                "gen-risk",
                target="T2",
                score=0.62,
                series="series-b",
                uncertainty=0.7,
                warning="high developability alert",
            ),
        ],
        experimental_results=[_assay_result("result-gen-1", "gen-1", generated.canonical_smiles)],
        budget=ResourceBudget(
            max_candidates=3,
            max_generated_candidates=2,
            max_assay_slots=2,
            max_review_hours=2,
        ),
    )

    assert run.metadata["deterministic_module_version"] == "portfolio_optimizer.v1.4.0"
    assert run.metadata["codex_generated_outputs"] is False
    assert run.status == "succeeded"
    assert run.recommended_selection_id == run.selections[0].selection_id
    assert len(run.selections[0].selected_candidate_ids) <= 3
    assert run.metadata["decision_memo"]["human_approval_required"] is True
    assert "safety" not in run.metadata["decision_memo"]["executive_summary"].lower()

    generated_candidate = next(
        candidate
        for candidate in run.metadata["input_candidates"]
        if candidate["portfolio_candidate_id"] == "gen-1"
    )
    assert generated_candidate["generated_without_direct_evidence"] is False
    assert generated_candidate["metadata"]["exact_experimental_evidence_result_ids"] == [
        "result-gen-1"
    ]

    risky_candidate = next(
        candidate
        for candidate in run.metadata["input_candidates"]
        if candidate["portfolio_candidate_id"] == "gen-risk"
    )
    assert "developability" in risky_candidate["risk_flags"]


def test_generated_portfolio_candidate_rejects_validation_claims() -> None:
    bad = _generated("gen-bad", target="T1", score=0.5, series="series-x", uncertainty=0.5)
    bad = bad.model_copy(update={"warnings": ["validated active"]})

    try:
        optimize_portfolio(
            program=Program(program_id="program-1", name="Program 1"),
            generated_molecules=[bad],
        )
    except ValueError as exc:
        assert "Generated portfolio candidates must not contain" in str(exc)
    else:
        raise AssertionError("Expected generated validation claim to be rejected")


def test_portfolio_optimize_cli_writes_deterministic_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    candidates = [_existing("A", target="T1", score=0.8, series="series-a")]
    generated = GeneratedMoleculeHypothesis(
        name="hyp-1",
        canonical_smiles="CCO",
        target_symbol="T2",
        generation_score=0.7,
        min_seed_similarity=0.35,
        max_seed_similarity=0.64,
        mean_seed_similarity=0.5,
        trace={"uncertainty_score": 0.8, "diversity_cluster": "series-b"},
        warnings=["in_silico_hypothesis_only"],
    )
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "disease": {"input_name": "Disease A", "canonical_name": "Disease A"},
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            }
        )
    )
    (run_dir / "generated_candidates.json").write_text(
        json.dumps({"retained_generated_molecules": [generated.model_dump(mode="json")]})
    )
    output = tmp_path / "portfolio.json"

    result = CliRunner().invoke(
        app,
        [
            "portfolio",
            "optimize",
            "--from-run",
            str(run_dir),
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["metadata"]["deterministic_module_version"] == "portfolio_optimizer.v1.4.0"
    assert payload["disease_name"] == "Disease A"
    assert payload["metadata"]["program"]["target_focus"] == ["T1", "T2"]
    assert payload["metadata"]["deterministic_selection"] is True


def test_artifact_builder_builds_candidates_from_existing_artifacts(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    existing = next(
        candidate for candidate in candidates if candidate.candidate_name == "Existing A"
    )

    assert existing.origin == "existing"
    assert existing.source_candidate_id == "CHEMBL-A"
    assert existing.evidence_score == 0.82
    assert existing.metadata["artifact_refs"]["candidate"] == "candidates.json"
    assert "generation_score" in existing.metadata["missing_data"]


def test_artifact_builder_marks_generated_without_direct_evidence(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    generated = next(
        candidate
        for candidate in candidates
        if candidate.portfolio_candidate_id == "gen-no-evidence"
    )

    assert generated.origin == "generated"
    assert generated.generated_without_direct_evidence is True
    assert generated.direct_experimental_evidence is False
    assert generated.experimental_support_score is None


def test_artifact_builder_propagates_developability_risk(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    risky = next(
        candidate for candidate in candidates if candidate.portfolio_candidate_id == "gen-risk"
    )

    assert risky.developability_score == 0.18
    assert "critical_developability_risk" in risky.blocking_risks
    assert "critical_developability_risk" in risky.risk_flags


def test_artifact_builder_links_only_exact_experimental_evidence(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    linked = next(
        candidate for candidate in candidates if candidate.portfolio_candidate_id == "gen-linked"
    )
    unlinked = next(
        candidate
        for candidate in candidates
        if candidate.portfolio_candidate_id == "gen-no-evidence"
    )

    assert linked.direct_experimental_evidence is True
    assert linked.generated_without_direct_evidence is False
    assert linked.metadata["exact_experimental_evidence_result_ids"] == ["result-linked"]
    assert unlinked.direct_experimental_evidence is False
    assert unlinked.metadata.get("exact_experimental_evidence_result_ids") is None


def test_artifact_builder_keeps_model_predictions_out_of_evidence(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    existing = next(
        candidate for candidate in candidates if candidate.candidate_name == "Existing A"
    )

    assert existing.predictive_model_score == 0.73
    assert existing.evidence_score == 0.82
    assert existing.metadata["model_predictions_are_not_evidence"] is True
    assert existing.metadata["artifact_refs"]["model_predictions"] == "model_predictions.json"


def test_artifact_builder_keeps_structure_score_out_of_binding_claims(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    generated = next(
        candidate for candidate in candidates if candidate.portfolio_candidate_id == "gen-linked"
    )

    assert generated.structure_score == 0.41
    assert generated.metadata["structure_score_is_not_binding_evidence"] is True
    assert "binding" not in " ".join(generated.risk_flags).lower()


def test_artifact_builder_records_conflicting_identifier_warnings(tmp_path: Path) -> None:
    _write_portfolio_artifacts(tmp_path, conflicting_prediction=True)

    candidates = build_portfolio_candidates_from_artifacts(tmp_path)
    existing = next(
        candidate for candidate in candidates if candidate.candidate_name == "Existing A"
    )

    assert "conflicting_identifiers" in existing.metadata["warnings"]
    assert existing.metadata["identifier_conflicts"][0]["field"] == "canonical_smiles"


def _write_portfolio_artifacts(
    run_dir: Path,
    *,
    conflicting_prediction: bool = False,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "disease": {"canonical_name": "Disease A"},
                "candidates": [
                    {
                        "name": "Existing A",
                        "identifiers": {"chembl": "CHEMBL-A"},
                        "canonical_smiles": "CCO",
                        "known_targets": ["T1"],
                        "mechanism_of_action": "T1 modulation",
                        "score": 0.82,
                        "score_breakdown": {
                            "final_score": 0.82,
                            "novelty_or_repurposing_value": 0.42,
                        },
                        "chemical_metadata": {
                            "chemical_series": "series-a",
                            "scaffold_id": "scaffold-a",
                        },
                    }
                ],
            }
        )
    )
    (run_dir / "generated_candidates.json").write_text(
        json.dumps(
            {
                "retained_generated_molecules": [
                    {
                        "generated_id": "gen-no-evidence",
                        "canonical_smiles": "CCN",
                        "conditioned_targets": ["T2"],
                        "generation_score": 0.66,
                        "diversity_cluster": "series-g",
                    },
                    {
                        "generated_id": "gen-linked",
                        "canonical_smiles": "CCC",
                        "conditioned_targets": ["T3"],
                        "generation_score": 0.72,
                        "diversity_cluster": "series-h",
                    },
                    {
                        "generated_id": "gen-risk",
                        "canonical_smiles": "CCCl",
                        "conditioned_targets": ["T4"],
                        "generation_score": 0.61,
                    },
                ]
            }
        )
    )
    (run_dir / "developability.json").write_text(
        json.dumps(
            {
                "assessments": [
                    {
                        "molecule_name": "gen-risk",
                        "developability_score": 0.18,
                        "risk_level": "critical",
                        "triage_recommendation": "high_risk_flags",
                        "toxicity_risk_flags": [
                            {
                                "category": "toxicity_risk",
                                "severity": "high",
                                "label": "toxicity alert",
                            }
                        ],
                    }
                ]
            }
        )
    )
    (run_dir / "experimental_evidence.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "result_id": "result-linked",
                        "candidate_id": "gen-linked",
                        "candidate_name": "gen-linked",
                        "canonical_smiles": "CCC",
                        "outcome_label": "inconclusive",
                        "qc_status": "passed",
                        "confidence": 0.7,
                    },
                    {
                        "result_id": "result-unlinked",
                        "candidate_id": "different-generated-id",
                        "canonical_smiles": "CCBr",
                        "outcome_label": "positive",
                        "qc_status": "passed",
                    },
                ]
            }
        )
    )
    (run_dir / "model_predictions.json").write_text(
        json.dumps(
            {
                "predictions": [
                    {
                        "prediction_id": "prediction-1",
                        "candidate_id": "CHEMBL-A",
                        "candidate_name": "Existing A",
                        "canonical_smiles": "CCN" if conflicting_prediction else "CCO",
                        "predicted_probability": 0.73,
                        "confidence": 0.8,
                        "not_evidence": True,
                    }
                ]
            }
        )
    )
    (run_dir / "structure_aware_assessments.json").write_text(
        json.dumps(
            {
                "structure_aware_assessments": [
                    {
                        "molecule_id": "gen-linked",
                        "consensus_score": 0.41,
                        "recommendation": "needs_structure_review",
                    }
                ]
            }
        )
    )
