from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

ResearchHypothesisType = Literal[
    "disease_target",
    "molecule_target",
    "molecule_disease",
    "mechanism",
    "generated_molecule",
    "scaffold_series",
    "developability_risk",
    "safety_risk",
    "assay_contradiction",
    "evidence_gap",
    "active_learning",
    "portfolio_decision",
]
ResearchHypothesisStatus = Literal[
    "proposed",
    "under_review",
    "accepted_for_planning",
    "rejected",
    "needs_more_evidence",
    "contradicted",
    "stale",
    "retired",
]
TestableQuestionType = Literal[
    "target_engagement",
    "pathway_modulation",
    "phenotypic_effect",
    "selectivity",
    "safety_liability",
    "developability",
    "mechanism_disambiguation",
    "contradiction_resolution",
    "evidence_gap_closure",
    "portfolio_decision",
]
FalsificationEvidenceType = Literal[
    "assay_result",
    "literature_evidence",
    "review_decision",
    "developability_assessment",
    "structure_assessment",
    "model_recalibration",
    "graph_update",
]
FalsificationDecisionImpact = Literal[
    "increase_priority",
    "decrease_priority",
    "retire_hypothesis",
    "require_more_data",
    "change_mechanism",
    "update_portfolio",
]
EvidenceGapType = Literal[
    "missing_target_evidence",
    "missing_molecule_target_evidence",
    "missing_literature",
    "missing_direct_experimental_result",
    "missing_selectivity_data",
    "missing_safety_data",
    "missing_developability_data",
    "missing_structure_context",
    "contradictory_results",
    "stale_model_prediction",
    "unreviewed_generated_molecule",
]
EvidenceGapSeverity = Literal["low", "medium", "high", "critical"]
HypothesisReviewDecisionValue = Literal[
    "accept_for_planning",
    "reject",
    "needs_more_evidence",
    "escalate",
    "retire",
    "hold",
]
HypothesisLifecycleEventType = Literal[
    "created",
    "updated",
    "reviewed",
    "accepted",
    "rejected",
    "contradicted",
    "supported",
    "retired",
    "made_stale",
    "revived",
]

HypothesisType = Literal[
    "mechanistic",
    "molecule_target",
    "generated_molecule_follow_up",
    "developability_risk",
    "assay_result_contradiction",
    "cross_program_scaffold_series",
    "evidence_gap",
    "active_learning",
    "portfolio_decision",
    "high_level_validation_question",
]
HypothesisReviewStatus = Literal[
    "draft",
    "needs_more_evidence",
    "ready_for_expert_review",
    "rejected",
    "retired",
]
ResearchQuestionType = Literal[
    "evidence_review",
    "contradiction_review",
    "developability_review",
    "portfolio_review",
    "active_learning_review",
    "validation_question",
]
ReviewDecision = Literal[
    "needs_more_evidence",
    "ready_for_expert_review",
    "rejected",
    "retired",
]

HYPOTHESIS_BOUNDARIES = [
    "A hypothesis is not evidence.",
    "A research question is not a lab protocol.",
    "A validation plan is not an experimental procedure.",
    "No medical advice, synthesis routes, lab protocols, reagents, dosing, or patient guidance.",
    "Codex may draft only from graph-backed evidence, with deterministic reference validation.",
]


class TimezoneAwareHypothesisModel(BaseModel):
    @field_validator(
        "created_at",
        "updated_at",
        "timestamp",
        "started_at",
        "completed_at",
        check_fields=False,
    )
    @classmethod
    def require_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class ResearchHypothesis(TimezoneAwareHypothesisModel):
    hypothesis_id: str
    hypothesis_type: ResearchHypothesisType
    title: str
    statement: str
    disease_entity_ids: list[str] = Field(default_factory=list)
    target_entity_ids: list[str] = Field(default_factory=list)
    molecule_entity_ids: list[str] = Field(default_factory=list)
    generated_molecule_entity_ids: list[str] = Field(default_factory=list)
    scaffold_entity_ids: list[str] = Field(default_factory=list)
    mechanism_entity_ids: list[str] = Field(default_factory=list)
    supporting_relation_ids: list[str] = Field(default_factory=list)
    contradicting_relation_ids: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    assay_result_ids: list[str] = Field(default_factory=list)
    literature_claim_ids: list[str] = Field(default_factory=list)
    model_prediction_ids: list[str] = Field(default_factory=list)
    review_decision_ids: list[str] = Field(default_factory=list)
    graph_path_ids: list[str] = Field(default_factory=list)
    support_score: float = Field(default=0.0, ge=0.0, le=1.0)
    contradiction_score: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty_score: float = Field(default=0.0, ge=0.0, le=1.0)
    testability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty_score: float = Field(default=0.0, ge=0.0, le=1.0)
    priority_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: ResearchHypothesisStatus = "proposed"
    limitations: list[str] = Field(default_factory=lambda: list(HYPOTHESIS_BOUNDARIES))
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_provenance_refs(self) -> ResearchHypothesis:
        provenance_refs = [
            *self.supporting_relation_ids,
            *self.contradicting_relation_ids,
            *self.source_artifact_ids,
            *self.evidence_item_ids,
            *self.assay_result_ids,
            *self.literature_claim_ids,
            *self.model_prediction_ids,
            *self.review_decision_ids,
            *self.graph_path_ids,
        ]
        if not provenance_refs:
            raise ValueError("hypotheses require at least one provenance reference")
        return self


class TestableResearchQuestion(BaseModel):
    question_id: str
    hypothesis_id: str
    question_text: str
    question_type: TestableQuestionType
    high_level_validation_category: str
    linked_entity_ids: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    expected_observation_if_supported: str
    expected_observation_if_not_supported: str
    ambiguity_notes: list[str] = Field(default_factory=list)
    forbidden_detail_check: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_procedural_lab_details(self) -> TestableResearchQuestion:
        from molecule_ranker.hypotheses.guardrails import detect_hypothesis_guardrail_violations

        text = " ".join(
            [
                self.question_text,
                self.high_level_validation_category,
                *self.required_context,
                self.expected_observation_if_supported,
                self.expected_observation_if_not_supported,
                *self.ambiguity_notes,
            ]
        )
        if detect_hypothesis_guardrail_violations(text):
            self.forbidden_detail_check = False
            raise ValueError("research questions must not contain procedural lab details")
        self.forbidden_detail_check = True
        return self


class FalsificationCriterion(BaseModel):
    criterion_id: str = Field(default_factory=lambda: f"criterion-{uuid4().hex[:12]}")
    hypothesis_id: str = ""
    criterion_text: str = ""
    evidence_type_needed: FalsificationEvidenceType = "graph_update"
    would_support: bool = False
    would_contradict: bool = True
    decision_impact: FalsificationDecisionImpact = "require_more_data"
    statement: str = ""
    graph_record_ids: list[str] = Field(default_factory=list)
    not_lab_protocol: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_statement(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "criterion_text" not in migrated and "statement" in migrated:
            migrated["criterion_text"] = migrated["statement"]
        if "statement" not in migrated and "criterion_text" in migrated:
            migrated["statement"] = migrated["criterion_text"]
        return migrated

    @model_validator(mode="after")
    def reject_procedural_text(self) -> FalsificationCriterion:
        from molecule_ranker.hypotheses.guardrails import detect_hypothesis_guardrail_violations

        if detect_hypothesis_guardrail_violations(self.criterion_text):
            self.not_lab_protocol = False
            raise ValueError("falsification criteria must not include procedural instructions")
        self.not_lab_protocol = True
        return self


class EvidenceGap(BaseModel):
    gap_id: str = Field(default_factory=lambda: f"gap-{uuid4().hex[:12]}")
    hypothesis_id: str = ""
    gap_type: EvidenceGapType = "missing_direct_experimental_result"
    description: str
    severity: EvidenceGapSeverity = "medium"
    suggested_high_level_resolution: str = "Review graph-backed context for missing evidence."
    linked_entity_ids: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)
    related_relation_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HypothesisReviewDecision(TimezoneAwareHypothesisModel):
    decision_id: str = Field(default_factory=lambda: f"decision-{uuid4().hex[:12]}")
    hypothesis_id: str = ""
    reviewer_id: str
    decision: HypothesisReviewDecisionValue
    rationale: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class HypothesisLifecycleEvent(TimezoneAwareHypothesisModel):
    event_id: str = Field(default_factory=lambda: f"hyp-event-{uuid4().hex[:12]}")
    hypothesis_id: str = ""
    event_type: HypothesisLifecycleEventType
    actor: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    actor_id: str | None = None
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_legacy_fields(self) -> HypothesisLifecycleEvent:
        if self.actor is None and self.actor_id is not None:
            self.actor = self.actor_id
        if not self.summary and self.reason:
            self.summary = self.reason
        return self


class HypothesisGenerationRun(TimezoneAwareHypothesisModel):
    generation_run_id: str
    project_id: str | None = None
    program_id: str | None = None
    graph_build_id: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    hypothesis_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan-{uuid4().hex[:12]}")
    research_question_id: str
    objective: str
    question_type: ResearchQuestionType
    recommended_evidence: list[str] = Field(default_factory=list)
    excluded_content: list[str] = Field(default_factory=lambda: list(HYPOTHESIS_BOUNDARIES[1:4]))
    not_experimental_procedure: bool = True
    prohibited_detail_count: int = 0

    @model_validator(mode="after")
    def count_prohibited_details(self) -> ValidationPlan:
        from molecule_ranker.hypotheses.validation import detect_hypothesis_guardrail_violations

        text = " ".join([self.objective, *self.recommended_evidence])
        self.prohibited_detail_count = len(detect_hypothesis_guardrail_violations(text))
        if self.prohibited_detail_count:
            self.not_experimental_procedure = False
        return self


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: f"hyp-{uuid4().hex[:16]}")
    hypothesis_type: HypothesisType
    title: str
    summary: str
    uncertainty: str
    entity_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    provenance_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    contradiction_relation_ids: list[str] = Field(default_factory=list)
    falsification_criteria: list[FalsificationCriterion] = Field(default_factory=list)
    validation_plan: ValidationPlan | None = None
    rank_score: float = Field(default=0.0, ge=0.0, le=1.0)
    review_status: HypothesisReviewStatus = "draft"
    not_evidence: bool = True
    generated_by: Literal["deterministic", "codex_draft"] = "deterministic"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lifecycle_events: list[HypothesisLifecycleEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=lambda: list(HYPOTHESIS_BOUNDARIES))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_hypothesis_boundary(self) -> Hypothesis:
        if not self.not_evidence:
            raise ValueError("hypotheses must not be promoted to evidence")
        if not self.uncertainty.strip():
            raise ValueError("hypotheses must include uncertainty")
        if not self.falsification_criteria:
            raise ValueError("hypotheses must include falsification criteria")
        if not self.evidence_gaps:
            raise ValueError("hypotheses must include evidence gaps")
        return self


class HypothesisSet(BaseModel):
    graph_id: str
    schema_version: str = "1.6"
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    limitations: list[str] = Field(default_factory=lambda: list(HYPOTHESIS_BOUNDARIES))


class ResearchQuestion(BaseModel):
    question_id: str = Field(default_factory=lambda: f"rq-{uuid4().hex[:12]}")
    hypothesis_id: str
    question_type: ResearchQuestionType
    question: str
    entity_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    validation_plan: ValidationPlan
    not_lab_protocol: bool = True
    review_status: HypothesisReviewStatus = "draft"


class ResearchQuestionSet(BaseModel):
    graph_id: str
    questions: list[ResearchQuestion] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    limitations: list[str] = Field(default_factory=lambda: list(HYPOTHESIS_BOUNDARIES))


class HypothesisReviewRecord(BaseModel):
    review_record_id: str = Field(default_factory=lambda: f"hyp-review-{uuid4().hex[:12]}")
    hypothesis_id: str
    reviewer_id: str
    decision: HypothesisReviewDecisionValue
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    promotes_to_evidence: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HypothesisValidationReport(BaseModel):
    status: Literal["pass", "fail"]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HypothesisCodexArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"codex-hypothesis-{uuid4().hex[:16]}")
    graph_id: str
    task_type: str
    status: str
    output_json: dict[str, Any] | None = None
    output_text: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
