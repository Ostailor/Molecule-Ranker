from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.hypotheses.lifecycle import HypothesisLifecycleManager
from molecule_ranker.hypotheses.store import HypothesisStore

from .schemas import (
    Hypothesis,
    HypothesisLifecycleEvent,
    HypothesisReviewDecision,
    HypothesisReviewRecord,
    ResearchHypothesis,
    ResearchHypothesisStatus,
)

__all__ = [
    "HypothesisReviewQueue",
    "HypothesisReviewService",
    "attach_hypotheses_to_review_workspace",
]


class HypothesisReviewQueue(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    review_records: list[HypothesisReviewRecord] = Field(default_factory=list)

    @classmethod
    def from_hypotheses(cls, hypotheses: list[Hypothesis]) -> HypothesisReviewQueue:
        return cls(hypotheses=[hypothesis.model_copy(deep=True) for hypothesis in hypotheses])

    def record_review(
        self,
        hypothesis_id: str,
        decision: HypothesisReviewDecision,
    ) -> HypothesisReviewRecord:
        for index, hypothesis in enumerate(self.hypotheses):
            if hypothesis.hypothesis_id != hypothesis_id:
                continue
            record = HypothesisReviewRecord(
                hypothesis_id=hypothesis_id,
                reviewer_id=decision.reviewer_id,
                decision=decision.decision,
                rationale=decision.rationale,
                confidence=decision.confidence,
            )
            updated = hypothesis.model_copy(
                update={
                    "review_status": decision.decision,
                    "lifecycle_events": [
                        *hypothesis.lifecycle_events,
                        HypothesisLifecycleEvent(
                            event_type="reviewed",
                            actor_id=decision.reviewer_id,
                            reason=decision.rationale,
                            metadata={"review_record_id": record.review_record_id},
                        ),
                    ],
                }
            )
            self.hypotheses[index] = updated
            self.review_records.append(record)
            return record
        raise KeyError(f"unknown hypothesis: {hypothesis_id}")


class HypothesisReviewService:
    """Connect human review decisions to persistent V1.6 hypothesis lifecycle state."""

    def __init__(
        self,
        store: HypothesisStore,
        *,
        require_generated_molecule_human_approval: bool = True,
    ) -> None:
        self.store = store
        self.lifecycle = HypothesisLifecycleManager(
            store,
            require_generated_molecule_human_approval=(
                require_generated_molecule_human_approval
            ),
        )

    def attach_hypotheses_to_workspace(
        self,
        workspace: Any,
        hypotheses: list[ResearchHypothesis] | None = None,
        *,
        hypothesis_ids: list[str] | None = None,
    ) -> Any:
        resolved = hypotheses or [
            self.store.get_hypothesis(hypothesis_id)
            for hypothesis_id in list(hypothesis_ids or [])
        ]
        return attach_hypotheses_to_review_workspace(workspace, resolved)

    def record_decision(
        self,
        hypothesis_id: str,
        *,
        reviewer_id: str,
        decision: str,
        rationale: str,
        confidence: float = 0.0,
        human_approval: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> HypothesisReviewDecision:
        if _is_codex_reviewer(reviewer_id) and decision == "accept_for_planning":
            raise ValueError("Codex cannot approve hypotheses")
        current = self.store.get_hypothesis(hypothesis_id)
        if (
            decision == "accept_for_planning"
            and current.hypothesis_type == "generated_molecule"
            and self.lifecycle.require_generated_molecule_human_approval
            and not human_approval
        ):
            raise ValueError(
                "Generated-molecule hypotheses require explicit human approval"
            )
        decision_metadata = {
            "review_decision_is_not_evidence": True,
            **dict(metadata or {}),
        }
        if human_approval:
            decision_metadata["human_approval"] = True
        review_decision = HypothesisReviewDecision(
            hypothesis_id=hypothesis_id,
            reviewer_id=reviewer_id,
            decision=decision,  # type: ignore[arg-type]
            rationale=rationale,
            confidence=confidence,
            metadata=decision_metadata,
        )
        self.store.add_review_decision(review_decision)
        patch = {
            "review_decision_ids": [
                *current.review_decision_ids,
                review_decision.decision_id,
            ],
        }
        self.lifecycle.transition_status(
            hypothesis_id,
            _status_for_review_decision(decision),
            actor=reviewer_id,
            summary=rationale,
            metadata={
                "decision_id": review_decision.decision_id,
                "decision": decision,
                **decision_metadata,
            },
            patch=patch,
        )
        return review_decision


def attach_hypotheses_to_review_workspace(
    workspace: Any,
    hypotheses: list[ResearchHypothesis],
) -> Any:
    """Return a workspace copy with hypothesis IDs and summaries in metadata."""

    hypothesis_ids = [hypothesis.hypothesis_id for hypothesis in hypotheses]
    metadata = dict(getattr(workspace, "metadata", {}))
    metadata["hypothesis_ids"] = _append_unique(
        [str(value) for value in metadata.get("hypothesis_ids", [])],
        hypothesis_ids,
    )
    metadata["hypotheses"] = [
        {
            "hypothesis_id": hypothesis.hypothesis_id,
            "hypothesis_type": hypothesis.hypothesis_type,
            "status": hypothesis.status,
            "title": hypothesis.title,
            "priority_score": hypothesis.priority_score,
            "not_evidence": True,
        }
        for hypothesis in hypotheses
    ]
    review_items = [
        _attach_matching_hypotheses_to_review_item(item, hypotheses)
        for item in getattr(workspace, "review_items", [])
    ]
    return workspace.model_copy(update={"metadata": metadata, "review_items": review_items})


def _attach_matching_hypotheses_to_review_item(
    item: Any,
    hypotheses: list[ResearchHypothesis],
) -> Any:
    candidate_refs = {
        str(getattr(item, "candidate_id", "")),
        str(getattr(item, "candidate_name", "")),
        *[str(symbol) for symbol in getattr(item, "target_symbols", [])],
    }
    matched = [
        hypothesis.hypothesis_id
        for hypothesis in hypotheses
        if candidate_refs
        & {
            *hypothesis.molecule_entity_ids,
            *hypothesis.generated_molecule_entity_ids,
            *hypothesis.target_entity_ids,
        }
    ]
    if not matched:
        return item
    metadata = dict(getattr(item, "metadata", {}))
    metadata["hypothesis_ids"] = _append_unique(
        [str(value) for value in metadata.get("hypothesis_ids", [])],
        matched,
    )
    metadata["hypothesis_review_boundary"] = (
        "Hypotheses linked for planning remain separate from evidence and review decisions."
    )
    return item.model_copy(update={"metadata": metadata})


def _status_for_review_decision(decision: str) -> ResearchHypothesisStatus:
    if decision == "accept_for_planning":
        return "accepted_for_planning"
    if decision == "reject":
        return "rejected"
    if decision == "needs_more_evidence":
        return "needs_more_evidence"
    if decision == "retire":
        return "retired"
    return "under_review"


def _append_unique(existing: list[str], new_values: list[str]) -> list[str]:
    values = list(existing)
    seen = set(values)
    for value in new_values:
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _is_codex_reviewer(reviewer_id: str) -> bool:
    normalized = reviewer_id.lower().replace("_", "-").replace(" ", "-")
    return "codex" in normalized
