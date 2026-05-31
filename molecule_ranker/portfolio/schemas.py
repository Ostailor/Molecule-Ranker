from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

CandidateOrigin = Literal["existing", "generated", "external"]
ObjectiveType = Literal["maximize", "minimize", "balance", "cover", "constrain"]
ObjectiveDirection = Literal["higher_is_better", "lower_is_better", "categorical"]
ConstraintViolationAction = Literal["reject", "penalize", "warn", "require_review"]
OptimizationAlgorithm = Literal[
    "greedy",
    "pareto",
    "weighted_sum",
    "integer_programming_optional",
    "scenario_comparison",
]
OptimizationStatus = Literal["queued", "running", "succeeded", "failed"]
StageGateDecision = Literal[
    "advance",
    "hold",
    "deprioritize",
    "reject",
    "needs_more_data",
    "none",
]
PortfolioBatchType = Literal[
    "expert_review_batch",
    "assay_triage_batch",
    "active_learning_batch",
    "structure_review_batch",
    "developability_review_batch",
]

SCORE_FIELDS = {
    "evidence_score",
    "generation_score",
    "developability_score",
    "experimental_support_score",
    "predictive_model_score",
    "structure_score",
    "experiment_readiness_score",
    "uncertainty_score",
    "novelty_score",
    "portfolio_score",
}


class TimezoneAwareModel(BaseModel):
    @field_validator("created_at", "updated_at", "started_at", "completed_at", check_fields=False)
    @classmethod
    def require_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class Program(TimezoneAwareModel):
    program_id: str
    name: str
    disease_focus: list[str] = Field(default_factory=list)
    target_focus: list[str] = Field(default_factory=list)
    description: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioCandidate(BaseModel):
    portfolio_candidate_id: str
    source_candidate_id: str | None = None
    candidate_name: str
    origin: CandidateOrigin
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    disease_name: str | None = None
    target_symbols: list[str] = Field(default_factory=list)
    mechanism_label: str | None = None
    chemical_series_id: str | None = None
    scaffold_id: str | None = None
    evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    generation_score: float | None = Field(default=None, ge=0.0, le=1.0)
    developability_score: float | None = Field(default=None, ge=0.0, le=1.0)
    experimental_support_score: float | None = Field(default=None, ge=0.0, le=1.0)
    predictive_model_score: float | None = Field(default=None, ge=0.0, le=1.0)
    structure_score: float | None = Field(default=None, ge=0.0, le=1.0)
    experiment_readiness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertainty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    novelty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    diversity_features: dict[str, Any] = Field(default_factory=dict)
    risk_flags: list[str] = Field(default_factory=list)
    blocking_risks: list[str] = Field(default_factory=list)
    review_status: str | None = None
    direct_experimental_evidence: bool = False
    generated_without_direct_evidence: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_generated_evidence_boundary(self) -> Self:
        if self.origin == "generated":
            self.generated_without_direct_evidence = not self.direct_experimental_evidence
            forbidden = {
                "safe",
                "active",
                "effective",
                "synthesizable",
                "validated",
                "clinical",
            }
            text = " ".join([*self.risk_flags, *self.blocking_risks]).lower()
            metadata_text = " ".join(str(value) for value in self.metadata.values()).lower()
            claimed = sorted(term for term in forbidden if term in text or term in metadata_text)
            if claimed:
                raise ValueError(
                    "Generated portfolio candidates must not contain safety, activity, "
                    f"effectiveness, synthesizability, or validation claims: {', '.join(claimed)}."
                )
        return self


class PortfolioObjective(BaseModel):
    objective_id: str
    name: str
    objective_type: ObjectiveType
    metric_name: str
    weight: float = Field(ge=0.0)
    direction: ObjectiveDirection
    hard: bool = False
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioConstraint(BaseModel):
    constraint_id: str
    name: str
    constraint_type: str
    value: Any
    hard: bool = False
    violation_action: ConstraintViolationAction
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceBudget(BaseModel):
    budget_id: str = "default-budget"
    name: str = "Default portfolio budget"
    max_candidates: int | None = Field(default=8, ge=0)
    max_existing_candidates: int | None = Field(default=None, ge=0)
    max_generated_candidates: int | None = Field(default=None, ge=0)
    max_total_cost: float | None = Field(default=None, ge=0.0)
    cost_units: str | None = None
    max_docking_jobs: int | None = Field(default=None, ge=0)
    max_assay_slots: int | None = Field(default=None, ge=0)
    max_review_hours: float | None = Field(default=None, ge=0.0)
    max_codex_tasks: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PortfolioSelection(BaseModel):
    selection_id: str
    selected_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    deferred_candidate_ids: list[str] = Field(default_factory=list)
    objective_scores: dict[str, float] = Field(default_factory=dict)
    constraint_violations: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_score: float = Field(ge=0.0, le=1.0)
    diversity_summary: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    uncertainty_summary: dict[str, Any] = Field(default_factory=dict)
    target_coverage: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("objective_scores")
    @classmethod
    def bound_objective_scores(cls, value: dict[str, float]) -> dict[str, float]:
        out_of_bounds = {key: score for key, score in value.items() if score < 0.0 or score > 1.0}
        if out_of_bounds:
            raise ValueError("objective_scores values must be bounded [0, 1]")
        return value


class PortfolioOptimizationRun(TimezoneAwareModel):
    optimization_run_id: str
    program_id: str | None = None
    project_id: str | None = None
    disease_name: str | None = None
    input_candidate_count: int = Field(ge=0)
    objectives: list[PortfolioObjective] = Field(default_factory=list)
    constraints: list[PortfolioConstraint] = Field(default_factory=list)
    budget: ResourceBudget
    algorithm: OptimizationAlgorithm
    status: OptimizationStatus
    selections: list[PortfolioSelection] = Field(default_factory=list)
    recommended_selection_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionScenario(BaseModel):
    scenario_id: str
    name: str
    description: str
    objective_overrides: dict[str, Any] = Field(default_factory=dict)
    constraint_overrides: dict[str, Any] = Field(default_factory=dict)
    budget_overrides: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    selection: PortfolioSelection | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StageGate(BaseModel):
    stage_gate_id: str
    name: str
    from_stage: str
    to_stage: str
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    decision: StageGateDecision | None = None
    rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProgramDecisionMemo(TimezoneAwareModel):
    memo_id: str
    program_id: str | None = None
    optimization_run_id: str
    title: str
    executive_summary: str
    selected_portfolio_summary: str
    key_tradeoffs: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
    human_approval_required: bool = False
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


_PROTOCOL_DETAIL_PATTERN = re.compile(
    r"\b("
    r"\d+(?:\.\d+)?\s*(?:nm|um|µm|mm|mg|ml|ul|µl|hours?|hrs?|minutes?|mins?|"
    r"seconds?|secs?|c|°c)"
    r"|concentration|temperature|timing|reagent|reagents|incubate|incubation|"
    r"pipette|wash|centrifuge|procedure|protocol|step-by-step"
    r")\b",
    re.IGNORECASE,
)


class PortfolioBatch(BaseModel):
    batch_id: str
    batch_type: PortfolioBatchType
    candidate_ids: list[str] = Field(default_factory=list)
    purpose: str
    high_level_followup_categories: list[str] = Field(default_factory=list)
    rationale: str
    required_approvals: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_protocol_details(self) -> Self:
        text = " ".join(
            [
                self.purpose,
                *self.high_level_followup_categories,
                self.rationale,
                *self.required_approvals,
                *self.warnings,
                *(str(value) for value in self.metadata.values()),
            ]
        )
        if _PROTOCOL_DETAIL_PATTERN.search(text):
            raise ValueError("PortfolioBatch must not include protocol-level details")
        return self


class Portfolio(BaseModel):
    """Internal optimizer input container for V1.4 portfolio analytics."""

    portfolio_id: str
    program: Program
    candidates: list[PortfolioCandidate] = Field(default_factory=list)
    objectives: list[PortfolioObjective] = Field(default_factory=list)
    constraints: list[PortfolioConstraint] = Field(default_factory=list)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SensitivityAnalysis(BaseModel):
    """Internal scenario comparison summary kept separate from required schemas."""

    baseline_selection_id: str | None = None
    scenarios: list[DecisionScenario] = Field(default_factory=list)
    robust_candidate_ids: list[str] = Field(default_factory=list)
    fragile_candidate_ids: list[str] = Field(default_factory=list)
    objective_sensitivities: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
