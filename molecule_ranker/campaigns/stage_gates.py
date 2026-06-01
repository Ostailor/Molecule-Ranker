from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.campaigns.schemas import CampaignExecutionEvent, CampaignWorkPackage

GateType = Literal[
    "campaign_approval",
    "generated_molecule_review",
    "assay_triage_approval",
    "budget_approval",
    "safety_review",
    "contradiction_resolution_review",
    "replan_approval",
    "stop_continue_decision",
]


def build_generated_molecule_review_gate(
    work_package: CampaignWorkPackage,
    *,
    require_human_review: bool = True,
) -> dict[str, Any]:
    blocking = ["generated_molecule_human_review_required"] if require_human_review else []
    return _gate(
        gate_type="generated_molecule_review",
        campaign_id=work_package.campaign_id,
        work_package_id=work_package.work_package_id,
        required_role=["scientific_reviewer"],
        required_permissions=["generated_molecule_review_gate"],
        required_artifacts=[
            *work_package.linked_hypothesis_ids,
            *work_package.linked_candidate_ids,
        ],
        required_review_decisions=["generated_molecule_review_decision"],
        blocking_conditions=blocking,
        rationale=(
            "Generated molecules require human review before assay-triage campaign "
            "packages by default."
        ),
    )


def build_safety_review_gate(work_package: CampaignWorkPackage) -> dict[str, Any]:
    return _gate(
        gate_type="safety_review",
        campaign_id=work_package.campaign_id,
        work_package_id=work_package.work_package_id,
        required_role=["safety_reviewer"],
        required_permissions=["campaign:approve_safety_review"],
        required_artifacts=[
            *work_package.linked_hypothesis_ids,
            *work_package.linked_candidate_ids,
        ],
        required_review_decisions=["safety_review_decision"],
        blocking_conditions=["safety_concern_requires_review"],
        rationale="Safety concerns require review before campaign continuation.",
    )


def build_budget_approval_gate(
    *,
    campaign_id: str,
    budget_check: dict[str, Any],
) -> dict[str, Any] | None:
    if budget_check.get("within_budget") is True:
        return None
    exceeded = [
        str(item)
        for item in budget_check.get("exceeded_dimensions", [])
        if str(item)
    ]
    warnings = [str(item) for item in budget_check.get("warnings", [])]
    return _gate(
        gate_type="budget_approval",
        campaign_id=campaign_id,
        work_package_id=None,
        required_role=["campaign_owner"],
        required_permissions=["campaign:approve_budget_exception"],
        required_artifacts=[],
        required_review_decisions=["budget_exception_decision"],
        blocking_conditions=["budget_exceeded"],
        rationale=(
            "Budget approval is required for exceeded dimensions: "
            f"{', '.join(exceeded) if exceeded else 'unknown'}."
        ),
        metadata={"budget_warnings": warnings, "exceeded_dimensions": exceeded},
    )


def build_campaign_approval_gate(campaign_id: str) -> dict[str, Any]:
    return _gate(
        gate_type="campaign_approval",
        campaign_id=campaign_id,
        work_package_id=None,
        required_role=["campaign_owner"],
        required_permissions=["campaign:approve"],
        required_artifacts=[],
        required_review_decisions=["campaign_approval_decision"],
        blocking_conditions=["human_approval_required"],
        rationale="Human campaign approval is required before marking a campaign active.",
    )


def approve_stage_gate(
    gate: dict[str, Any],
    *,
    actor: str,
    actor_role: str,
    actor_permissions: list[str],
    decision: Literal["approved", "rejected", "blocked"],
    rationale: str,
) -> tuple[dict[str, Any], CampaignExecutionEvent]:
    if actor.lower().startswith("codex") or actor_role.lower() in {"codex", "assistant"}:
        raise ValueError("Codex cannot approve campaign stage gates.")
    required_roles = set(_list(gate.get("required_role")))
    required_permissions = set(_list(gate.get("required_permissions")))
    if required_roles and actor_role not in required_roles:
        raise ValueError(f"Actor role {actor_role} is not allowed to approve this gate.")
    if not required_permissions.issubset(set(actor_permissions)):
        missing = sorted(required_permissions - set(actor_permissions))
        raise ValueError(f"Actor lacks required gate permissions: {', '.join(missing)}")

    before = dict(gate)
    approved = {
        **gate,
        "approval_status": decision,
        "rationale": rationale,
        "approved_by": actor,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    event = CampaignExecutionEvent(
        event_id=_stable_id("campaign-stage-gate-event", gate.get("gate_id"), actor, decision),
        campaign_id=str(gate["campaign_id"]),
        work_package_id=gate.get("work_package_id"),
        event_type="stage_gate_decided",
        actor=actor,
        summary=f"Stage gate {gate.get('gate_type')} decision recorded as {decision}.",
        before=before,
        after={
            "approval_status": decision,
            "rationale": rationale,
            "approved_by": actor,
        },
        metadata={
            "gate_id": gate.get("gate_id"),
            "gate_type": gate.get("gate_type"),
            "codex_approved": False,
        },
    )
    approved["audit_event"] = event.model_dump(mode="json")
    return approved, event


def _gate(
    *,
    gate_type: GateType,
    campaign_id: str,
    work_package_id: str | None,
    required_role: list[str],
    required_permissions: list[str],
    required_artifacts: list[str],
    required_review_decisions: list[str],
    blocking_conditions: list[str],
    rationale: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate_id = _stable_id("campaign-gate", campaign_id, work_package_id, gate_type)
    return {
        "gate_id": gate_id,
        "gate_type": gate_type,
        "campaign_id": campaign_id,
        "work_package_id": work_package_id,
        "required_role": required_role,
        "required_permissions": required_permissions,
        "required_artifacts": required_artifacts,
        "required_review_decisions": required_review_decisions,
        "blocking_conditions": blocking_conditions,
        "approval_status": "pending",
        "rationale": rationale,
        "audit_event": None,
        "metadata": {
            "planning_gate": True,
            "not_codex_approvable": True,
            **(metadata or {}),
        },
    }


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts if part is not None) or prefix
    return f"{prefix}:{uuid5(NAMESPACE_URL, raw).hex[:12]}"


__all__ = [
    "GateType",
    "approve_stage_gate",
    "build_budget_approval_gate",
    "build_campaign_approval_gate",
    "build_generated_molecule_review_gate",
    "build_safety_review_gate",
]
