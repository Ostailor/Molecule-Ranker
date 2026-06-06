from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class V3SubagentSpec(BaseModel):
    subagent_name: str
    responsibility: str
    required: bool = True
    coordinates: bool = False
    validates_source_backed_evidence: bool = False
    review_gate_required: bool = False
    final_result_bundle_review: bool = False
    campaign_activation_allowed: bool = False
    external_write_allowed: bool = False
    approved_tools_only: bool = True


class V3AgentOrchestration(BaseModel):
    workflow_type: str
    coordinator_subagent: str = "ProgramManagerSubagent"
    guardrail_final_review_subagent: str = "GuardrailSentinelSubagent"
    generated_outputs_require_review_gates: bool = True
    campaign_activation_allowed: bool = False
    codex_approved_tools_only: bool = True
    subagents: list[V3SubagentSpec] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def subagent(self, subagent_name: str) -> V3SubagentSpec:
        for subagent in self.subagents:
            if subagent.subagent_name == subagent_name:
                return subagent
        raise KeyError(f"subagent not present: {subagent_name}")


class V3AgentOrchestrationValidation(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)


def build_v3_default_orchestration(
    *,
    workflow_type: str = "full_discovery_loop",
    generation_enabled: bool = False,
    biologics_enabled: bool = False,
    integrations_enabled: bool = False,
) -> V3AgentOrchestration:
    subagents = [
        V3SubagentSpec(
            subagent_name="ProgramManagerSubagent",
            responsibility="Coordinate the full discovery loop and human-governed handoffs.",
            coordinates=True,
        ),
        V3SubagentSpec(
            subagent_name="EvidenceReviewerSubagent",
            responsibility="Validate source-backed evidence and keep evidence separate.",
            validates_source_backed_evidence=True,
        ),
    ]
    if generation_enabled:
        subagents.append(
            V3SubagentSpec(
                subagent_name="MoleculeDesignerSubagent",
                responsibility="Prepare generated small-molecule computational hypotheses.",
                required=False,
                review_gate_required=True,
            )
        )
    if biologics_enabled:
        subagents.append(
            V3SubagentSpec(
                subagent_name="BiologicsEngineerSubagent",
                responsibility="Coordinate governed biologics discovery-loop planning.",
                required=False,
                review_gate_required=True,
            )
        )
    subagents.extend(
        [
            V3SubagentSpec(
                subagent_name="DevelopabilitySafetySubagent",
                responsibility="Review developability triage as planning information only.",
            ),
            V3SubagentSpec(
                subagent_name="GraphReasonerSubagent",
                responsibility="Separate graph inference from imported evidence.",
            ),
            V3SubagentSpec(
                subagent_name="HypothesisPlannerSubagent",
                responsibility="Prepare hypotheses for expert review gates.",
                review_gate_required=True,
            ),
            V3SubagentSpec(
                subagent_name="PortfolioStrategistSubagent",
                responsibility="Draft portfolio recommendations without approval authority.",
            ),
            V3SubagentSpec(
                subagent_name="CampaignPlannerSubagent",
                responsibility="Draft campaign plans; never activate campaigns.",
                campaign_activation_allowed=False,
            ),
            V3SubagentSpec(
                subagent_name="EvaluationValidatorSubagent",
                responsibility="Validate evaluation artifacts separately from evidence.",
            ),
            V3SubagentSpec(
                subagent_name="GuardrailSentinelSubagent",
                responsibility="Review guardrails and the final result bundle.",
                final_result_bundle_review=True,
            ),
            V3SubagentSpec(
                subagent_name="PlatformOperatorSubagent",
                responsibility="Track runtime, artifacts, reproducibility, and platform status.",
            ),
        ]
    )
    if integrations_enabled:
        subagents.append(
            V3SubagentSpec(
                subagent_name="IntegrationOperatorSubagent",
                responsibility="Participate only for integration visibility or approved syncs.",
                required=False,
                external_write_allowed=False,
            )
        )
    return V3AgentOrchestration(
        workflow_type=workflow_type,
        subagents=subagents,
        rules=[
            "ProgramManagerSubagent coordinates the full_discovery_loop.",
            "EvidenceReviewerSubagent validates source-backed evidence.",
            "Generated outputs require review gates.",
            "CampaignPlannerSubagent cannot activate campaigns.",
            "IntegrationOperatorSubagent participates only when integrations are enabled.",
            "GuardrailSentinelSubagent always reviews the final result bundle.",
            "Codex planning must use approved tools only.",
        ],
        metadata={
            "generation_enabled": generation_enabled,
            "biologics_enabled": biologics_enabled,
            "integrations_enabled": integrations_enabled,
        },
    )


def validate_v3_orchestration(
    orchestration: V3AgentOrchestration,
) -> V3AgentOrchestrationValidation:
    subagents = {subagent.subagent_name: subagent for subagent in orchestration.subagents}
    issues: list[str] = []
    for required_subagent in [
        "ProgramManagerSubagent",
        "EvidenceReviewerSubagent",
        "DevelopabilitySafetySubagent",
        "GraphReasonerSubagent",
        "HypothesisPlannerSubagent",
        "PortfolioStrategistSubagent",
        "CampaignPlannerSubagent",
        "EvaluationValidatorSubagent",
        "GuardrailSentinelSubagent",
        "PlatformOperatorSubagent",
    ]:
        if required_subagent not in subagents:
            issues.append(f"missing required subagent: {required_subagent}")
    if orchestration.coordinator_subagent != "ProgramManagerSubagent":
        issues.append("ProgramManagerSubagent must coordinate")
    if not subagents.get("ProgramManagerSubagent", V3SubagentSpec(
        subagent_name="ProgramManagerSubagent",
        responsibility="missing",
    )).coordinates:
        issues.append("ProgramManagerSubagent must be marked as coordinator")
    evidence_reviewer = subagents.get("EvidenceReviewerSubagent")
    if evidence_reviewer is None or not evidence_reviewer.validates_source_backed_evidence:
        issues.append("EvidenceReviewerSubagent must validate source-backed evidence")
    sentinel = subagents.get("GuardrailSentinelSubagent")
    if sentinel is None or not sentinel.final_result_bundle_review:
        issues.append("GuardrailSentinelSubagent must review final result bundle")
    if not orchestration.generated_outputs_require_review_gates:
        issues.append("generated outputs must require review gates")
    if orchestration.campaign_activation_allowed:
        issues.append("CampaignPlannerSubagent cannot activate campaigns")
    if not orchestration.codex_approved_tools_only:
        issues.append("Codex planning must use approved tools only")
    for subagent in subagents.values():
        if subagent.subagent_name in {
            "MoleculeDesignerSubagent",
            "BiologicsEngineerSubagent",
        } and not subagent.review_gate_required:
            issues.append(f"{subagent.subagent_name} requires a review gate")
        if subagent.subagent_name == "IntegrationOperatorSubagent" and (
            subagent.external_write_allowed
        ):
            issues.append("IntegrationOperatorSubagent cannot allow external writes by default")
    return V3AgentOrchestrationValidation(valid=not issues, issues=issues)


__all__ = [
    "V3AgentOrchestration",
    "V3AgentOrchestrationValidation",
    "V3SubagentSpec",
    "build_v3_default_orchestration",
    "validate_v3_orchestration",
]
