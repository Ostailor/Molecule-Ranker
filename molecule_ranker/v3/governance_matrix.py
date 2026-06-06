from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

V3_HUMAN_GOVERNANCE_MATRIX_VERSION = "v3.human-governance-matrix.1"
GovernanceRequirementType = Literal["approval", "review"]


class V3GovernanceRequirement(BaseModel):
    requirement_id: str
    requirement_type: GovernanceRequirementType
    label: str
    human_required: bool = True
    codex_self_approval_allowed: bool = False
    rationale: str


class V3HumanGovernanceMatrix(BaseModel):
    matrix_version: str = V3_HUMAN_GOVERNANCE_MATRIX_VERSION
    product_name: str = "molecule-ranker"
    product_version: str = "3.0.0"
    approval_required: list[V3GovernanceRequirement] = Field(default_factory=list)
    review_required: list[V3GovernanceRequirement] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)

    def requirement(self, requirement_id: str) -> V3GovernanceRequirement:
        for requirement in [*self.approval_required, *self.review_required]:
            if requirement.requirement_id == requirement_id:
                return requirement
        raise KeyError(f"governance requirement not found: {requirement_id}")

    def requires_human_approval(self, requirement_id: str) -> bool:
        return any(
            requirement.requirement_id == requirement_id and requirement.human_required
            for requirement in self.approval_required
        )

    def requires_human_review(self, requirement_id: str) -> bool:
        return any(
            requirement.requirement_id == requirement_id and requirement.human_required
            for requirement in self.review_required
        )


class V3GovernanceDecisionValidation(BaseModel):
    valid: bool
    action_id: str
    actor_type: str
    human_approval_required: bool
    human_review_required: bool
    issues: list[str] = Field(default_factory=list)


def build_v3_human_governance_matrix() -> V3HumanGovernanceMatrix:
    return V3HumanGovernanceMatrix(
        approval_required=[
            _approval("external_write", "External write"),
            _approval(
                "generated_molecule_advancement",
                "Generated molecule advancement",
            ),
            _approval(
                "generated_antibody_advancement",
                "Generated antibody advancement",
            ),
            _approval("campaign_activation", "Campaign activation"),
            _approval("stage_gate_approval", "Stage gate approval"),
            _approval("high_cost_job", "High-cost job"),
            _approval("destructive_action", "Destructive action"),
            _approval("policy_override", "Policy override"),
            _approval("autonomy_escalation", "Autonomy escalation"),
            _approval("tool_package_approval", "Tool package approval"),
            _approval(
                "support_bundle_with_logs_transcripts",
                "Support bundle with logs/transcripts",
            ),
            _approval("write_approved_live_workflow", "write_approved_live workflow"),
        ],
        review_required=[
            _review("generated_hypotheses", "Generated hypotheses"),
            _review("generated_antibodies", "Generated antibodies"),
            _review(
                "critical_developability_safety_risks",
                "Critical developability/safety risks",
            ),
            _review("unresolved_contradictions", "Unresolved contradictions"),
            _review("high_risk_model_predictions", "High-risk model predictions"),
            _review(
                "weak_structure_docking_conclusions",
                "Weak structure/docking conclusions",
            ),
            _review("campaign_plan_finalization", "Campaign plan finalization"),
            _review(
                "portfolio_selection_finalization",
                "Portfolio selection finalization",
            ),
        ],
        rules=[
            "Human approval is required for governed write-capable or irreversible actions.",
            "Human review is required for generated and high-risk planning outputs.",
            "Codex cannot self-approve governed actions.",
            "Campaign activation and stage-gate approval remain human-owned.",
            (
                "The governance matrix is a platform/workflow control artifact, "
                "not clinical validation."
            ),
        ],
    )


def validate_v3_governance_decision(
    *,
    action_id: str,
    actor_type: str,
    approval_ids: list[str] | None = None,
    matrix: V3HumanGovernanceMatrix | None = None,
) -> V3GovernanceDecisionValidation:
    active_matrix = matrix or build_v3_human_governance_matrix()
    human_approval_required = active_matrix.requires_human_approval(action_id)
    human_review_required = active_matrix.requires_human_review(action_id)
    issues: list[str] = []

    if human_approval_required and not approval_ids:
        issues.append("Human approval is required")
    if human_approval_required and actor_type == "codex":
        issues.append("Codex self-approval is blocked")

    return V3GovernanceDecisionValidation(
        valid=not issues,
        action_id=action_id,
        actor_type=actor_type,
        human_approval_required=human_approval_required,
        human_review_required=human_review_required,
        issues=issues,
    )


def write_v3_human_governance_matrix(
    matrix: V3HumanGovernanceMatrix,
    *,
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "v3_human_governance_matrix.json"
    markdown_path = output_dir / "v3_human_governance_matrix.md"
    payload = matrix.model_dump(mode="json")
    payload["approval_requirements"] = payload["approval_required"]
    payload["review_requirements"] = payload["review_required"]
    payload["approval_required"] = [
        requirement.requirement_id for requirement in matrix.approval_required
    ]
    payload["review_required"] = [
        requirement.requirement_id for requirement in matrix.review_required
    ]
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_v3_human_governance_matrix_markdown(matrix), encoding="utf-8")
    return {
        "v3_human_governance_matrix.json": str(json_path),
        "v3_human_governance_matrix.md": str(markdown_path),
    }


def render_v3_human_governance_matrix_markdown(
    matrix: V3HumanGovernanceMatrix,
) -> str:
    lines = [
        "# V3 Human Governance Matrix",
        "",
        f"- Product: `{matrix.product_name}`",
        f"- Product version: `{matrix.product_version}`",
        f"- Matrix version: `{matrix.matrix_version}`",
        "",
        "## Human Approval Required",
        "",
        *[
            f"- `{requirement.requirement_id}`: {requirement.label}"
            for requirement in matrix.approval_required
        ],
        "",
        "## Human Review Required",
        "",
        "Human review is required for generated hypotheses and generated antibodies.",
        "",
        *[
            f"- `{requirement.requirement_id}`: {requirement.label}"
            for requirement in matrix.review_required
        ],
        "",
        "## Rules",
        "",
        *[f"- {rule}" for rule in matrix.rules],
        "",
    ]
    return "\n".join(lines)


def _approval(requirement_id: str, label: str) -> V3GovernanceRequirement:
    return V3GovernanceRequirement(
        requirement_id=requirement_id,
        requirement_type="approval",
        label=label,
        rationale=f"{label} requires explicit human approval under V3 governance.",
    )


def _review(requirement_id: str, label: str) -> V3GovernanceRequirement:
    return V3GovernanceRequirement(
        requirement_id=requirement_id,
        requirement_type="review",
        label=label,
        rationale=f"{label} requires human review before downstream reliance.",
    )


__all__ = [
    "V3GovernanceDecisionValidation",
    "V3GovernanceRequirement",
    "V3HumanGovernanceMatrix",
    "V3_HUMAN_GOVERNANCE_MATRIX_VERSION",
    "build_v3_human_governance_matrix",
    "render_v3_human_governance_matrix_markdown",
    "validate_v3_governance_decision",
    "write_v3_human_governance_matrix",
]
