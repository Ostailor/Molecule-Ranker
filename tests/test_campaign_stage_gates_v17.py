from __future__ import annotations

import pytest

from molecule_ranker.campaigns.schemas import CampaignWorkPackage
from molecule_ranker.campaigns.stage_gates import (
    approve_stage_gate,
    build_budget_approval_gate,
    build_generated_molecule_review_gate,
    build_safety_review_gate,
)


def test_generated_molecule_gate_required_before_assay_triage() -> None:
    package = _package(
        "pkg-generated-assay",
        "assay_triage_request",
        metadata={"generated_molecule": True},
    )

    gate = build_generated_molecule_review_gate(package)

    assert gate["gate_type"] == "generated_molecule_review"
    assert gate["approval_status"] == "pending"
    assert gate["blocking_conditions"] == ["generated_molecule_human_review_required"]
    assert "generated_molecule_review_gate" in gate["required_permissions"]
    assert gate["required_review_decisions"] == ["generated_molecule_review_decision"]


def test_safety_gate_blocks_until_review() -> None:
    package = _package(
        "pkg-safety",
        "developability_review",
        warnings=["critical safety concern"],
        blocking_reasons=["Safety review required."],
    )

    gate = build_safety_review_gate(package)

    assert gate["gate_type"] == "safety_review"
    assert "safety_reviewer" in gate["required_role"]
    assert gate["blocking_conditions"] == ["safety_concern_requires_review"]
    assert gate["approval_status"] == "pending"


def test_budget_gate_created_for_exceeded_campaign() -> None:
    gate = build_budget_approval_gate(
        campaign_id="campaign-1",
        budget_check={
            "within_budget": False,
            "exceeded_dimensions": ["assay_slots"],
            "warnings": ["assay_slots exceeds configured campaign budget limit."],
        },
    )

    assert gate is not None
    assert gate["gate_type"] == "budget_approval"
    assert gate["blocking_conditions"] == ["budget_exceeded"]
    assert gate["required_permissions"] == ["campaign:approve_budget_exception"]
    assert "assay_slots" in gate["rationale"]


def test_codex_cannot_approve_gate_and_human_approval_writes_audit_event() -> None:
    gate = build_safety_review_gate(_package("pkg-safety", "developability_review"))

    with pytest.raises(ValueError, match="Codex cannot approve"):
        approve_stage_gate(
            gate,
            actor="codex",
            actor_role="assistant",
            actor_permissions=["campaign:approve_safety_review"],
            decision="approved",
            rationale="Assistant approval.",
        )

    approved, event = approve_stage_gate(
        gate,
        actor="reviewer-1",
        actor_role="safety_reviewer",
        actor_permissions=["campaign:approve_safety_review"],
        decision="approved",
        rationale="Human review completed.",
    )

    assert approved["approval_status"] == "approved"
    assert approved["audit_event"]["event_type"] == "stage_gate_decided"
    assert event.event_type == "stage_gate_decided"
    assert event.actor == "reviewer-1"
    assert event.after is not None
    assert event.after["approval_status"] == "approved"


def _package(
    package_id: str,
    package_type: str,
    *,
    metadata: dict[str, object] | None = None,
    warnings: list[str] | None = None,
    blocking_reasons: list[str] | None = None,
) -> CampaignWorkPackage:
    return CampaignWorkPackage(
        work_package_id=package_id,
        campaign_id="campaign-1",
        objective_ids=["objective-1"],
        package_type=package_type,  # type: ignore[arg-type]
        title=f"{package_id} title",
        description="High-level campaign stage-gate package.",
        linked_candidate_ids=["candidate-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="planning review",
        dependencies=[],
        required_approvals=[],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=None,
        estimated_compute_units=None,
        estimated_assay_slots=None,
        status="proposed",
        blocking_reasons=blocking_reasons or [],
        warnings=warnings or [],
        metadata=metadata or {},
    )
