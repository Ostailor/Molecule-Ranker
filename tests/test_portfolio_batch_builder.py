from __future__ import annotations

import pytest
from pydantic import ValidationError

from molecule_ranker.portfolio.batch_builder import (
    build_assay_triage_batch,
    build_expert_review_batch,
)
from molecule_ranker.portfolio.schemas import PortfolioBatch, PortfolioCandidate


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    review_status: str | None = None,
    risk_flags: list[str] | None = None,
    blocking_risks: list[str] | None = None,
    uncertainty_score: float = 0.4,
    readiness_score: float = 0.7,
) -> PortfolioCandidate:
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        canonical_smiles="CCO",
        target_symbols=["T1"],
        evidence_score=0.6,
        developability_score=0.7,
        experimental_support_score=0.5,
        predictive_model_score=0.6,
        structure_score=0.5,
        experiment_readiness_score=readiness_score,
        uncertainty_score=uncertainty_score,
        novelty_score=0.5,
        risk_flags=risk_flags or [],
        blocking_risks=blocking_risks or [],
        review_status=review_status,
        direct_experimental_evidence=origin != "generated",
        metadata={},
    )


def test_expert_review_batch_created() -> None:
    batch = build_expert_review_batch(
        [
            _candidate("existing-1", uncertainty_score=0.2),
            _candidate("generated-1", origin="generated", uncertainty_score=0.9),
        ],
        max_candidates=2,
    )

    assert batch.batch_type == "expert_review_batch"
    assert batch.candidate_ids == ["generated-1", "existing-1"]
    assert "risk" in batch.rationale
    assert batch.metadata["deterministic_batch"] is True


def test_generated_candidates_require_approval_for_assay_batch() -> None:
    batch = build_assay_triage_batch(
        [
            _candidate("existing-1", readiness_score=0.8),
            _candidate("generated-unreviewed", origin="generated", readiness_score=0.95),
            _candidate(
                "generated-reviewed",
                origin="generated",
                review_status="approved",
                readiness_score=0.9,
            ),
        ]
    )

    assert "generated-unreviewed" not in batch.candidate_ids
    assert "generated-reviewed" in batch.candidate_ids
    assert batch.required_approvals == ["generated_candidate_review_approval"]
    assert (
        batch.metadata["excluded_reasons"]["generated-unreviewed"]
        == "generated_review_approval_required"
    )


def test_batch_contains_no_protocol_details() -> None:
    batch = build_assay_triage_batch([_candidate("existing-1")])
    serialized = " ".join(
        [
            batch.purpose,
            batch.rationale,
            *batch.high_level_followup_categories,
            *batch.warnings,
        ]
    ).lower()

    for forbidden in ("37 c", "reagent", "incubate", "procedure", "protocol"):
        assert forbidden not in serialized

    with pytest.raises(ValidationError, match="protocol-level details"):
        PortfolioBatch(
            batch_id="bad-batch",
            batch_type="assay_triage_batch",
            candidate_ids=["existing-1"],
            purpose="Bad batch",
            high_level_followup_categories=["incubate at 37 C"],
            rationale="Contains operating detail.",
        )


def test_high_risk_candidates_excluded_by_default() -> None:
    batch = build_expert_review_batch(
        [
            _candidate("moderate-risk", risk_flags=["admet_review"]),
            _candidate("critical-risk", blocking_risks=["critical_developability_risk"]),
        ]
    )

    assert batch.candidate_ids == ["moderate-risk"]
    assert batch.metadata["excluded_reasons"]["critical-risk"] == "high_risk_excluded"
