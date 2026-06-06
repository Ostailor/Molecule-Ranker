from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from molecule_ranker.v3.governance_contract import (
    FORBIDDEN_OUTPUTS,
    REQUIRED_GUARDRAILS,
    REQUIRED_HUMAN_GOVERNANCE_POINTS,
)
from molecule_ranker.v3.result_contract import REQUIRED_RESULT_ARTIFACTS

V3_PRODUCT_CONTRACT_VERSION = "v3.product-contract.1"

V3SupportedWorkflow = Literal[
    "disease_to_ranked_candidates",
    "disease_to_generated_hypotheses",
    "disease_to_review_workspace",
    "disease_to_portfolio_and_campaign",
    "biologics_discovery_loop",
    "integration_sync_loop",
    "prospective_evaluation_loop",
    "full_discovery_loop",
]
V3SupportedMode = Literal[
    "mocked",
    "dry_run",
    "read_only_live",
    "write_approved_live",
]
V3CodexAutonomy = Literal["execute_with_approval"]

SUPPORTED_WORKFLOWS: list[V3SupportedWorkflow] = [
    "disease_to_ranked_candidates",
    "disease_to_generated_hypotheses",
    "disease_to_review_workspace",
    "disease_to_portfolio_and_campaign",
    "biologics_discovery_loop",
    "integration_sync_loop",
    "prospective_evaluation_loop",
    "full_discovery_loop",
]
SUPPORTED_MODES: list[V3SupportedMode] = [
    "mocked",
    "dry_run",
    "read_only_live",
    "write_approved_live",
]


class V3ProductContract(BaseModel):
    product_contract_version: str = V3_PRODUCT_CONTRACT_VERSION
    product_name: str = "molecule-ranker"
    product_version: str = "3.0.0"
    product_positioning: str = (
        "autonomous discovery operating system for internal research planning"
    )
    supported_workflows: list[V3SupportedWorkflow] = Field(
        default_factory=lambda: list(SUPPORTED_WORKFLOWS)
    )
    default_workflow: V3SupportedWorkflow = "full_discovery_loop"
    supported_modes: list[V3SupportedMode] = Field(
        default_factory=lambda: list(SUPPORTED_MODES)
    )
    default_mode: V3SupportedMode = "dry_run"
    default_generation_enabled: bool = False
    default_antibody_generation_enabled: bool = False
    default_external_writes_enabled: bool = False
    default_codex_autonomy: V3CodexAutonomy = "execute_with_approval"
    required_guardrails: list[str] = Field(default_factory=lambda: list(REQUIRED_GUARDRAILS))
    required_human_governance_points: list[str] = Field(
        default_factory=lambda: list(REQUIRED_HUMAN_GOVERNANCE_POINTS)
    )
    required_result_artifacts: list[str] = Field(
        default_factory=lambda: list(REQUIRED_RESULT_ARTIFACTS)
    )
    forbidden_outputs: list[str] = Field(default_factory=lambda: list(FORBIDDEN_OUTPUTS))
    limitations: list[str] = Field(
        default_factory=lambda: [
            "For internal research planning only.",
            (
                "V3 validation artifacts are software/autonomy validation artifacts, "
                "not clinical validation."
            ),
            (
                "No medical advice, no patient treatment guidance, no dosing guidance, "
                "no lab protocols, or no synthesis instructions."
            ),
            "Generated molecules and generated antibodies are computational hypotheses only.",
            (
                "Generated assets carry no claims of binding, activity, safety, efficacy, "
                "manufacturability, or therapeutic value."
            ),
            "Codex may operate only through approved tools and cannot create scientific truth.",
            "External writes are disabled by default and require human governance approval.",
        ]
    )

    @model_validator(mode="after")
    def enforce_v3_contract_invariants(self) -> V3ProductContract:
        issues: list[str] = []
        if self.product_name != "molecule-ranker":
            issues.append("product_name must be molecule-ranker")
        if self.product_version != "3.0.0":
            issues.append("product_version must be 3.0.0")
        if self.product_positioning != (
            "autonomous discovery operating system for internal research planning"
        ):
            issues.append("product_positioning must match V3 positioning")
        if self.default_workflow not in self.supported_workflows:
            issues.append("default_workflow must be in supported_workflows")
        if self.default_mode not in self.supported_modes:
            issues.append("default_mode must be in supported_modes")
        if self.default_mode != "dry_run":
            issues.append("default_mode must remain dry_run")
        if self.default_generation_enabled is not False:
            issues.append("default_generation_enabled must remain false")
        if self.default_antibody_generation_enabled is not False:
            issues.append("default_antibody_generation_enabled must remain false")
        if self.default_external_writes_enabled is not False:
            issues.append("default_external_writes_enabled must remain false")
        if self.default_codex_autonomy != "execute_with_approval":
            issues.append("default_codex_autonomy must be execute_with_approval")
        required_artifacts = set(REQUIRED_RESULT_ARTIFACTS)
        missing_artifacts = sorted(required_artifacts - set(self.required_result_artifacts))
        if missing_artifacts:
            issues.append(
                "required_result_artifacts missing: " + ", ".join(missing_artifacts)
            )
        if issues:
            raise ValueError("; ".join(issues))
        return self


def get_v3_product_contract() -> V3ProductContract:
    return V3ProductContract()


def v3_product_contract_payload() -> dict[str, Any]:
    return get_v3_product_contract().model_dump(mode="json")


__all__ = [
    "SUPPORTED_MODES",
    "SUPPORTED_WORKFLOWS",
    "V3CodexAutonomy",
    "V3ProductContract",
    "V3SupportedMode",
    "V3SupportedWorkflow",
    "V3_PRODUCT_CONTRACT_VERSION",
    "get_v3_product_contract",
    "v3_product_contract_payload",
]
