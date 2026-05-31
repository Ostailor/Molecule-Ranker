from __future__ import annotations

from molecule_ranker.portfolio.schemas import PortfolioCandidate
from molecule_ranker.portfolio.stage_gates import build_stage_gate


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    evidence_score: float | None = 0.7,
    direct_evidence: bool = False,
    review_status: str | None = None,
    structure_score: float | None = 0.7,
    metadata: dict[str, object] | None = None,
    risk_flags: list[str] | None = None,
) -> PortfolioCandidate:
    generated = origin == "generated"
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        target_symbols=["T1"],
        mechanism_label="T1 modulation",
        chemical_series_id="series-a",
        scaffold_id="scaffold-a",
        evidence_score=None if generated else evidence_score,
        generation_score=0.8 if generated else None,
        developability_score=0.8,
        experimental_support_score=0.4 if direct_evidence else None,
        structure_score=structure_score,
        experiment_readiness_score=0.8,
        diversity_features={},
        risk_flags=list(risk_flags or []),
        blocking_risks=[],
        review_status=review_status,
        direct_experimental_evidence=direct_evidence,
        generated_without_direct_evidence=generated and not direct_evidence,
        metadata={"portfolio_selection_status": "selected", **dict(metadata or {})},
    )


def test_generated_requires_review_approval_for_assay_candidate() -> None:
    gate = build_stage_gate(
        _candidate("generated", origin="generated", direct_evidence=True),
        from_stage="expert_review",
        to_stage="assay_candidate",
    )

    assert gate.decision == "needs_more_data"
    assert "expert_reviewer" in gate.required_approvals
    review_criterion = next(item for item in gate.criteria if item["name"] == "review_approval")
    assert review_criterion["passed"] is False


def test_docking_only_cannot_advance() -> None:
    gate = build_stage_gate(
        _candidate(
            "docking-only",
            evidence_score=None,
            structure_score=0.9,
            metadata={"docking_score": -8.2, "docking_score_only": True},
        ),
        from_stage="computational_triage",
        to_stage="assay_candidate",
    )

    assert gate.decision == "hold"
    assert any(
        item["name"] == "docking_score_not_sole_basis" and item["passed"] is False
        for item in gate.criteria
    )


def test_failed_qc_blocks_advance() -> None:
    gate = build_stage_gate(
        _candidate(
            "failed-qc",
            direct_evidence=True,
            metadata={"qc_statuses": ["failed"]},
        ),
        from_stage="assay_tested",
        to_stage="followup_candidate",
    )

    assert gate.decision == "reject"
    assert any(
        item["name"] == "assay_result_status" and item["passed"] is False for item in gate.criteria
    )


def test_human_approval_recorded() -> None:
    gate = build_stage_gate(
        _candidate("needs-approval", risk_flags=["developability_alert"]),
        from_stage="expert_review",
        to_stage="followup_candidate",
        require_human_approval=True,
    )

    assert "program_lead" in gate.required_approvals
    assert gate.metadata["audit_event"] is True
    assert gate.metadata["codex_generated_decision"] is False
    assert gate.decision in {"needs_more_data", "hold"}
