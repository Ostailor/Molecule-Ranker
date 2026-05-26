from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, field_validator, model_validator

REVIEW_LIMITATIONS = [
    "No operational chemistry or laboratory execution details are provided.",
    "Review decisions are expert triage labels, not clinical conclusions.",
    "Model-generated scores do not establish safety, efficacy, binding, or synthesizability.",
    "Generated molecules are unvalidated hypotheses and are not claimed to be active.",
    "No molecule is claimed to cure, treat, or prevent disease.",
    "No clinical-use or experimental-execution instructions are provided.",
]

ReviewerRole = Literal[
    "medicinal_chemist",
    "pharmacologist",
    "computational_chemist",
    "biologist",
    "founder",
    "reviewer",
]
CandidateOrigin = Literal["existing", "generated"]
PriorityBucket = Literal[
    "high_priority",
    "medium_priority",
    "low_priority",
    "reject_suggested",
    "needs_review",
]
ReviewStatus = Literal[
    "pending",
    "in_review",
    "accepted",
    "deprioritized",
    "rejected",
    "needs_more_data",
    "escalated",
]
DecisionValue = Literal[
    "accept_for_followup",
    "deprioritize",
    "reject",
    "needs_more_data",
    "escalate_to_expert",
    "hold",
]
CommentType = Literal[
    "general",
    "evidence_question",
    "chemistry_concern",
    "biology_concern",
    "literature_note",
    "developability_note",
    "generation_note",
    "safety_note",
]
FollowupRequestType = Literal[
    "rerun_with_more_literature",
    "rerun_with_more_targets",
    "stricter_developability",
    "structure_check",
    "docking_check",
    "analog_generation",
    "expert_review",
    "validation_handoff",
]
RequestPriority = Literal["low", "medium", "high"]
RequestStatus = Literal["open", "completed", "cancelled"]


class TimezoneAwareModel(BaseModel):
    @field_validator("created_at", "generated_at", "timestamp", check_fields=False)
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class Reviewer(TimezoneAwareModel):
    reviewer_id: str
    name: str | None = None
    role: ReviewerRole | str | None = None
    organization: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewItem(TimezoneAwareModel):
    review_item_id: str = ""
    run_id: str
    disease_name: str
    candidate_id: str
    candidate_name: str
    candidate_origin: CandidateOrigin
    target_symbols: list[str] = Field(default_factory=list)
    canonical_smiles: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    literature_summary: dict[str, Any] = Field(default_factory=dict)
    developability_summary: dict[str, Any] = Field(default_factory=dict)
    generation_summary: dict[str, Any] | None = None
    risk_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    priority_bucket: PriorityBucket
    review_status: ReviewStatus
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_review_item_id(self) -> Self:
        if not self.review_item_id:
            self.review_item_id = _slug_id("review-item", self.run_id, self.candidate_id)
        return self

    @property
    def item_id(self) -> str:
        return self.review_item_id

    @property
    def display_name(self) -> str:
        return self.candidate_name

    @property
    def candidate_kind(self) -> str:
        return self.candidate_origin

    @property
    def direct_evidence_available(self) -> bool:
        return self.candidate_origin == "existing" and bool(self.evidence_summary)

    @property
    def model_scores(self) -> dict[str, float | None]:
        return {"final_score": self.score, "confidence": self.confidence}


class ReviewerDecision(TimezoneAwareModel):
    decision_id: str = ""
    review_item_id: str
    reviewer: Reviewer
    decision: DecisionValue
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    decision_factors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_decision_id(self) -> Self:
        if not self.decision_id:
            self.decision_id = _hashed_id(
                "decision",
                self.review_item_id,
                self.reviewer.reviewer_id,
                self.decision,
                self.created_at.isoformat(),
            )
        return self


class ReviewerComment(TimezoneAwareModel):
    comment_id: str = ""
    review_item_id: str
    reviewer: Reviewer
    comment_text: str = Field(min_length=1)
    comment_type: CommentType
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_comment_id(self) -> Self:
        if not self.comment_id:
            self.comment_id = _hashed_id(
                "comment",
                self.review_item_id,
                self.reviewer.reviewer_id,
                self.comment_type,
                self.comment_text,
                self.created_at.isoformat(),
            )
        return self


class FollowupRequest(TimezoneAwareModel):
    request_id: str = ""
    review_item_id: str
    requested_by: Reviewer
    request_type: FollowupRequestType
    request_text: str = Field(min_length=1)
    priority: RequestPriority
    status: RequestStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_request_id(self) -> Self:
        if not self.request_id:
            self.request_id = _hashed_id(
                "followup",
                self.review_item_id,
                self.requested_by.reviewer_id,
                self.request_type,
                self.request_text,
                self.created_at.isoformat(),
            )
        return self


class CandidateDossier(TimezoneAwareModel):
    dossier_id: str = ""
    review_item_id: str
    disease_name: str
    candidate_name: str
    candidate_origin: CandidateOrigin
    executive_summary: str
    evidence_sections: list[dict[str, Any]] = Field(default_factory=list)
    risk_sections: list[dict[str, Any]] = Field(default_factory=list)
    reviewer_decisions: list[ReviewerDecision] = Field(default_factory=list)
    reviewer_comments: list[ReviewerComment] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(REVIEW_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_dossier_id(self) -> Self:
        if not self.dossier_id:
            self.dossier_id = _hashed_id(
                "dossier",
                self.review_item_id,
                self.candidate_name,
                self.generated_at.isoformat(),
            )
        return self


class CandidateComparison(TimezoneAwareModel):
    comparison_id: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    comparison_table: list[dict[str, Any]] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    shared_targets: list[str] = Field(default_factory=list)
    unique_targets: dict[str, list[str]] = Field(default_factory=dict)
    evidence_strength_comparison: dict[str, Any] = Field(default_factory=dict)
    literature_comparison: dict[str, Any] = Field(default_factory=dict)
    developability_comparison: dict[str, Any] = Field(default_factory=dict)
    generation_comparison: dict[str, Any] = Field(default_factory=dict)
    risk_comparison: dict[str, Any] = Field(default_factory=dict)
    recommendation_summary: str
    limitations: list[str] = Field(default_factory=lambda: list(REVIEW_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_comparison_id(self) -> Self:
        if not self.comparison_id:
            candidate_ids = [
                str(candidate.get("review_item_id") or candidate.get("candidate_id"))
                for candidate in self.candidates
            ]
            self.comparison_id = _hashed_id(
                "comparison",
                *candidate_ids,
                self.generated_at.isoformat(),
            )
        return self


class ValidationHandoff(TimezoneAwareModel):
    handoff_id: str = ""
    review_item_id: str
    candidate_name: str
    candidate_origin: CandidateOrigin
    disease_name: str
    target_symbols: list[str] = Field(default_factory=list)
    validation_questions: list[str] = Field(default_factory=list)
    suggested_assay_classes: list[str] = Field(default_factory=list)
    required_expert_reviews: list[str] = Field(default_factory=list)
    key_risks_to_check: list[str] = Field(default_factory=list)
    evidence_packet_paths: dict[str, str] = Field(default_factory=dict)
    disclaimer: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_handoff_and_fill_id(self) -> Self:
        _reject_protocol_content(
            [
                *self.validation_questions,
                *self.suggested_assay_classes,
                *self.required_expert_reviews,
                *self.key_risks_to_check,
                self.disclaimer,
            ]
        )
        if not self.handoff_id:
            self.handoff_id = _hashed_id(
                "handoff",
                self.review_item_id,
                self.candidate_name,
                self.created_at.isoformat(),
            )
        return self


class ReviewAuditEvent(TimezoneAwareModel):
    event_id: str = ""
    event_type: str
    actor: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    object_type: str
    object_id: str
    summary: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_event_id(self) -> Self:
        if not self.event_id:
            self.event_id = _hashed_id(
                "audit",
                self.event_type,
                self.actor,
                self.object_type,
                self.object_id,
                self.timestamp.isoformat(),
            )
        return self


class ReviewWorkspace(TimezoneAwareModel):
    workspace_id: str = ""
    run_id: str
    disease_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    review_items: list[ReviewItem] = Field(default_factory=list)
    decisions: list[ReviewerDecision] = Field(default_factory=list)
    comments: list[ReviewerComment] = Field(default_factory=list)
    followup_requests: list[FollowupRequest] = Field(default_factory=list)
    audit_events: list[ReviewAuditEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_workspace_id(self) -> Self:
        if not self.workspace_id:
            self.workspace_id = _slug_id("workspace", self.run_id, self.disease_name)
        return self


class ReviewQueue(TimezoneAwareModel):
    items: list[ReviewItem] = Field(default_factory=list)

    def get_item(self, review_item_id: str) -> ReviewItem:
        for item in self.items:
            if item.review_item_id == review_item_id:
                return item
        raise ValueError(f"Unknown review item: {review_item_id}")


class ExpertFeedback(TimezoneAwareModel):
    feedback_id: str = ""
    reviewer_id: str = ""
    candidate_id: str = ""
    candidate_name: str = ""
    decision: DecisionValue
    rationale: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_score_override: None = None
    source_workspace_id: str = ""
    review_item_id: str = ""
    reviewer: Reviewer | None = None
    ranking_signal: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_feedback_id(self) -> Self:
        if not self.reviewer_id and self.reviewer is not None:
            self.reviewer_id = self.reviewer.reviewer_id
        if not self.ranking_signal:
            self.ranking_signal = _feedback_ranking_signal(self.decision)
        if not self.feedback_id:
            self.feedback_id = _hashed_id(
                "feedback",
                self.source_workspace_id,
                self.candidate_id,
                self.candidate_name,
                self.reviewer_id,
                self.decision,
                self.created_at.isoformat(),
            )
        return self


class FeedbackIngestionResult(TimezoneAwareModel):
    workspace_id: str
    feedback: list[ExpertFeedback] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: list(REVIEW_LIMITATIONS))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


FollowUpCheck = FollowupRequest
ReviewerDecisionLabel = DecisionValue


def _slug_id(prefix: str, *parts: object) -> str:
    slugs = [_slug(part) for part in parts if str(part or "").strip()]
    return "-".join([prefix, *slugs])


def _hashed_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}-{uuid5(NAMESPACE_URL, raw).hex[:16]}"


def _slug(value: object) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"


def _feedback_ranking_signal(decision: str) -> str:
    if decision == "accept_for_followup":
        return "promote_for_expert_review"
    if decision in {"needs_more_data", "hold", "escalate_to_expert"}:
        return "needs_more_evidence"
    if decision == "reject":
        return "exclude_from_future_review"
    return "deprioritize_for_review"


def _reject_protocol_content(values: list[str]) -> None:
    forbidden_patterns = [
        r"\breagent\b",
        r"\breagents\b",
        r"\breaction condition",
        r"\bsynthesis route",
        r"\bdosage\b",
        r"\bdose\b",
        r"\bmg/kg\b",
        r"\btemperature\b",
        r"\b\d+\s*(?:°c|c\b|celsius)\b",
        r"\bstep\s*\d+\b",
        r"\bstep-by-step\b",
        r"\bincubat",
        r"\badminister",
    ]
    text = "\n".join(values).lower()
    if any(re.search(pattern, text) for pattern in forbidden_patterns):
        raise ValueError(
            "ValidationHandoff must not include lab protocols, dosages, synthesis routes, "
            "reagents, temperatures, reaction conditions, or procedural steps."
        )
