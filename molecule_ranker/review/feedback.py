from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from molecule_ranker.review.audit import audit_event
from molecule_ranker.review.schemas import (
    ExpertFeedback,
    FeedbackIngestionResult,
    ReviewerDecision,
    ReviewItem,
    ReviewWorkspace,
)

EXPERT_REVIEW_FEEDBACK_LABEL = "expert review feedback"
POSITIVE_DECISIONS = {"accept_for_followup"}
CAUTION_DECISIONS = {"needs_more_data", "escalate_to_expert", "hold"}
NEGATIVE_DECISIONS = {"deprioritize", "reject"}


class FeedbackIngestionAgent:
    def build_feedback(self, workspace: ReviewWorkspace) -> FeedbackIngestionResult:
        feedback = feedback_from_workspace(workspace)
        workspace.audit_events.append(
            audit_event(
                event_type="feedback_ingested",
                actor="FeedbackIngestionAgent",
                object_type="ReviewWorkspace",
                object_id=workspace.workspace_id,
                summary=f"Ingested {len(feedback)} expert feedback signals.",
                after={"feedback_count": len(feedback)},
            )
        )
        return FeedbackIngestionResult(workspace_id=workspace.workspace_id, feedback=feedback)


class FeedbackStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def in_memory_from_workspace(workspace: ReviewWorkspace) -> list[ExpertFeedback]:
        return feedback_from_workspace(workspace)

    def save_from_workspace(self, workspace: ReviewWorkspace) -> list[ExpertFeedback]:
        feedback = feedback_from_workspace(workspace)
        self.save_many(feedback)
        return feedback

    def save_many(self, feedback: list[ExpertFeedback]) -> None:
        with self._connect() as connection:
            for item in feedback:
                self._insert_feedback(connection, item)

    def query(
        self,
        *,
        candidate_name: str | None = None,
        inchikey: str | None = None,
        target: str | None = None,
        disease: str | None = None,
    ) -> list[ExpertFeedback]:
        clauses: list[str] = []
        params: list[str] = []
        if candidate_name:
            clauses.append("lower(candidate_name) = lower(?)")
            params.append(candidate_name)
        if inchikey:
            clauses.append("lower(inchikey) = lower(?)")
            params.append(inchikey)
        if target:
            clauses.append("target_symbols_json like ?")
            params.append(f'%"{target}"%')
        if disease:
            clauses.append("lower(disease_name) = lower(?)")
            params.append(disease)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select payload_json from expert_feedback
                {where}
                order by created_at, feedback_id
                """,
                tuple(params),
            ).fetchall()
        return [ExpertFeedback.model_validate_json(row["payload_json"]) for row in rows]

    def export_json(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        feedback = self.query()
        path.write_text(
            json.dumps(
                {
                    "feedback": [item.model_dump(mode="json") for item in feedback],
                    "limitations": [
                        (
                            "Expert feedback is ranking context only and is not "
                            "experimental evidence."
                        )
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return path

    def import_json(self, input_path: str | Path) -> list[ExpertFeedback]:
        payload = json.loads(Path(input_path).read_text())
        raw_feedback = payload.get("feedback") if isinstance(payload, dict) else payload
        if not isinstance(raw_feedback, list):
            raise ValueError("Feedback import must contain a feedback list.")
        feedback = [ExpertFeedback.model_validate(item) for item in raw_feedback]
        self.save_many(feedback)
        return feedback

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists expert_feedback (
                    feedback_id text primary key,
                    reviewer_id text not null,
                    candidate_id text not null,
                    candidate_name text not null,
                    inchikey text,
                    disease_name text,
                    target_symbols_json text not null,
                    decision text not null,
                    confidence real not null,
                    source_workspace_id text not null,
                    created_at text not null,
                    payload_json text not null
                );

                create index if not exists idx_expert_feedback_candidate
                    on expert_feedback(candidate_name, inchikey, candidate_id);
                create index if not exists idx_expert_feedback_context
                    on expert_feedback(disease_name, decision, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _insert_feedback(
        self,
        connection: sqlite3.Connection,
        feedback: ExpertFeedback,
    ) -> None:
        metadata = feedback.metadata
        target_symbols = metadata.get("target_symbols")
        connection.execute(
            """
            insert or replace into expert_feedback (
                feedback_id,
                reviewer_id,
                candidate_id,
                candidate_name,
                inchikey,
                disease_name,
                target_symbols_json,
                decision,
                confidence,
                source_workspace_id,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback.feedback_id,
                feedback.reviewer_id,
                feedback.candidate_id,
                feedback.candidate_name,
                str(metadata.get("inchikey") or ""),
                str(metadata.get("disease_name") or ""),
                json.dumps(target_symbols if isinstance(target_symbols, list) else []),
                feedback.decision,
                feedback.confidence,
                feedback.source_workspace_id,
                feedback.created_at.isoformat(),
                feedback.model_dump_json(),
            ),
        )


def feedback_from_workspace(workspace: ReviewWorkspace) -> list[ExpertFeedback]:
    items = {item.review_item_id: item for item in workspace.review_items}
    feedback: list[ExpertFeedback] = []
    for decision in workspace.decisions:
        item = items.get(decision.review_item_id)
        if item is None:
            continue
        feedback.append(_feedback_from_decision(decision, item, workspace))
    return feedback


def apply_feedback_to_review_item(
    item: ReviewItem,
    feedback: list[ExpertFeedback],
    *,
    enable_feedback_prior: bool,
    feedback_weight: float = 0.05,
    require_same_disease_for_feedback: bool = True,
) -> ReviewItem:
    if not enable_feedback_prior:
        return item
    relevant = _matching_feedback(
        item,
        feedback,
        require_same_disease_for_feedback=require_same_disease_for_feedback,
    )
    if not relevant:
        return item
    current_priority = item.priority_bucket
    signals = {_signal_class(signal.decision) for signal in relevant}
    conflicting = "positive" in signals and bool(signals & {"negative", "caution"})
    if conflicting:
        priority = "needs_review"
    elif "negative" in signals:
        priority = (
            "reject_suggested"
            if any(feedback_item.decision == "reject" for feedback_item in relevant)
            else "low_priority"
        )
    elif "caution" in signals:
        priority = "needs_review"
    else:
        priority = _promote_priority(current_priority, feedback_weight)
    metadata = {
        **item.metadata,
        "feedback_context": {
            "source_label": EXPERT_REVIEW_FEEDBACK_LABEL,
            "feedback_count": len(relevant),
            "decisions": [feedback_item.decision for feedback_item in relevant],
            "feedback_ids": [feedback_item.feedback_id for feedback_item in relevant],
            "conflicting_feedback": conflicting,
            "confidence_annotation": _confidence_annotation(relevant),
            "does_not_create_evidence": True,
        },
    }
    warnings = list(item.warnings)
    if EXPERT_REVIEW_FEEDBACK_LABEL not in warnings:
        warnings.append(EXPERT_REVIEW_FEEDBACK_LABEL)
    if conflicting and "Conflicting expert feedback requires review." not in warnings:
        warnings.append("Conflicting expert feedback requires review.")
    return item.model_copy(
        update={
            "priority_bucket": priority,
            "warnings": warnings,
            "metadata": metadata,
        }
    )


def _feedback_from_decision(
    decision: ReviewerDecision,
    item: ReviewItem,
    workspace: ReviewWorkspace,
) -> ExpertFeedback:
    return ExpertFeedback(
        reviewer_id=decision.reviewer.reviewer_id,
        candidate_id=item.candidate_id,
        candidate_name=item.candidate_name,
        decision=decision.decision,
        rationale=decision.rationale,
        tags=list(decision.decision_factors),
        confidence=decision.confidence,
        source_workspace_id=workspace.workspace_id,
        review_item_id=decision.review_item_id,
        reviewer=decision.reviewer,
        ranking_signal=_ranking_signal(decision.decision),
        metadata={
            "decision_id": decision.decision_id,
            "review_item_id": decision.review_item_id,
            "candidate_origin": item.candidate_origin,
            "disease_name": item.disease_name,
            "target_symbols": list(item.target_symbols),
            "inchikey": _inchikey(item),
            "source_label": EXPERT_REVIEW_FEEDBACK_LABEL,
            "score_boundary": (
                "Expert feedback is a separate ranking signal and does not become "
                "biomedical EvidenceItem evidence."
            ),
        },
    )


def _matching_feedback(
    item: ReviewItem,
    feedback: list[ExpertFeedback],
    *,
    require_same_disease_for_feedback: bool,
) -> list[ExpertFeedback]:
    matches: list[ExpertFeedback] = []
    item_inchikey = _inchikey(item)
    item_targets = {target.upper() for target in item.target_symbols}
    for feedback_item in feedback:
        metadata = feedback_item.metadata
        if (
            require_same_disease_for_feedback
            and str(metadata.get("disease_name")) != item.disease_name
        ):
            continue
        feedback_targets = {str(target).upper() for target in metadata.get("target_symbols", [])}
        same_target = bool(item_targets & feedback_targets) if feedback_targets else True
        same_name = feedback_item.candidate_name.lower() == item.candidate_name.lower()
        same_id = bool(
            feedback_item.candidate_id and feedback_item.candidate_id == item.candidate_id
        )
        same_inchikey = bool(item_inchikey and item_inchikey == metadata.get("inchikey"))
        if same_target and (same_name or same_id or same_inchikey):
            matches.append(feedback_item)
    return matches


def _promote_priority(priority: str, feedback_weight: float) -> str:
    if feedback_weight <= 0:
        return priority
    if priority == "low_priority":
        return "medium_priority"
    if priority in {"medium_priority", "needs_review"}:
        return "high_priority"
    return priority


def _confidence_annotation(feedback: list[ExpertFeedback]) -> dict[str, Any]:
    if not feedback:
        return {}
    return {
        "mean_reviewer_confidence": round(
            sum(item.confidence for item in feedback) / len(feedback),
            3,
        ),
        "note": (
            "Reviewer confidence is an annotation on expert review feedback, not "
            "experimental confidence."
        ),
    }


def _signal_class(decision: str) -> str:
    if decision in POSITIVE_DECISIONS:
        return "positive"
    if decision in CAUTION_DECISIONS:
        return "caution"
    return "negative"


def _ranking_signal(decision: str) -> str:
    if decision == "accept_for_followup":
        return "promote_for_expert_review"
    if decision in {"needs_more_data", "hold", "escalate_to_expert"}:
        return "needs_more_evidence"
    if decision == "reject":
        return "exclude_from_future_review"
    return "deprioritize_for_review"


def _inchikey(item: ReviewItem) -> str:
    raw = item.metadata.get("inchikey")
    if raw:
        return str(raw)
    identifiers = item.metadata.get("identifiers")
    if isinstance(identifiers, dict):
        return str(identifiers.get("inchikey") or identifiers.get("inchi_key") or "")
    chemical = item.metadata.get("chemical_metadata")
    if isinstance(chemical, dict):
        return str(chemical.get("inchikey") or chemical.get("inchi_key") or "")
    return ""
