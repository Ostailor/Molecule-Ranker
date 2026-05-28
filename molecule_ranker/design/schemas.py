from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

DesiredModality = Literal[
    "small_molecule",
    "degrader_like",
    "covalent_like_disabled_by_default",
    "fragment",
]
DesiredAction = Literal["inhibitor", "agonist", "antagonist", "modulator", "unknown"]
ActionSource = Literal["retrieved_mechanism", "literature_claim", "expert_review", "unknown"]


class DesignObjectiveV2(BaseModel):
    objective_id: str
    disease_name: str
    target_symbol: str
    target_identifiers: dict[str, str] = Field(default_factory=dict)
    desired_modality: DesiredModality = "small_molecule"
    desired_action: DesiredAction | None = "unknown"
    action_source: ActionSource = "unknown"
    seed_ids: list[str] = Field(default_factory=list)
    scaffold_ids: list[str] = Field(default_factory=list)
    optimization_goals: list[dict[str, Any]] = Field(default_factory=list)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    soft_constraints: dict[str, Any] = Field(default_factory=dict)
    forbidden_patterns: list[str] = Field(default_factory=list)
    target_context: dict[str, Any] = Field(default_factory=dict)
    evidence_context: dict[str, Any] = Field(default_factory=dict)
    uncertainty_context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_objective_contract(self) -> Self:
        required_hard = {
            "valid_molecule",
            "allowed_elements",
            "no_critical_alerts",
            "duplicate_rejection",
            "generated_label",
        }
        missing_hard = sorted(required_hard - set(self.hard_constraints))
        if missing_hard:
            raise ValueError(
                "DesignObjectiveV2 hard_constraints missing required keys: "
                + ", ".join(missing_hard)
            )

        required_soft = {
            "target_relevance",
            "seed_similarity_range",
            "novelty",
            "developability",
            "experimental_gap",
            "literature_context",
            "diversity",
        }
        missing_soft = sorted(required_soft - set(self.soft_constraints))
        if missing_soft:
            raise ValueError(
                "DesignObjectiveV2 soft_constraints missing required keys: "
                + ", ".join(missing_soft)
            )

        if self.action_source == "unknown" and self.desired_action not in {None, "unknown"}:
            raise ValueError("Unknown action_source cannot carry a specific desired_action.")
        if self.action_source != "unknown" and self.desired_action in {None, "unknown"}:
            raise ValueError("Known action_source requires a specific desired_action.")
        return self
