from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.review.feedback import (
    CAUTION_DECISIONS,
    NEGATIVE_DECISIONS,
    POSITIVE_DECISIONS,
)
from molecule_ranker.review.schemas import ReviewerDecision, ReviewItem, ReviewWorkspace


class ReviewMetrics(BaseModel):
    workspace_id: str
    run_id: str
    disease_name: str
    total_review_items: int = 0
    reviewed_count: int = 0
    pending_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    deprioritized_count: int = 0
    needs_more_data_count: int = 0
    accepted_by_origin: dict[str, int] = Field(default_factory=dict)
    accepted_by_target: dict[str, int] = Field(default_factory=dict)
    accepted_by_priority_bucket: dict[str, int] = Field(default_factory=dict)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    top_recurring_risk_flags: list[dict[str, int | str]] = Field(default_factory=list)
    time_to_decision: dict[str, Any] = Field(default_factory=dict)
    reviewer_activity_summary: dict[str, dict[str, Any]] = Field(default_factory=dict)
    feedback_conflict_count: int = 0


def compute_review_metrics(workspace: ReviewWorkspace) -> ReviewMetrics:
    items_by_id = {item.review_item_id: item for item in workspace.review_items}
    latest_decisions = _latest_decisions_by_item(workspace.decisions)
    accepted_items = [
        items_by_id[review_item_id]
        for review_item_id, decision in latest_decisions.items()
        if review_item_id in items_by_id and decision.decision == "accept_for_followup"
    ]
    decision_counts = Counter(decision.decision for decision in latest_decisions.values())
    reviewed_count = len(latest_decisions)
    total_items = len(workspace.review_items)
    return ReviewMetrics(
        workspace_id=workspace.workspace_id,
        run_id=workspace.run_id,
        disease_name=workspace.disease_name,
        total_review_items=total_items,
        reviewed_count=reviewed_count,
        pending_count=max(total_items - reviewed_count, 0),
        accepted_count=decision_counts.get("accept_for_followup", 0),
        rejected_count=decision_counts.get("reject", 0),
        deprioritized_count=decision_counts.get("deprioritize", 0),
        needs_more_data_count=decision_counts.get("needs_more_data", 0),
        accepted_by_origin=_accepted_by_origin(accepted_items),
        accepted_by_target=_accepted_by_target(accepted_items),
        accepted_by_priority_bucket=_accepted_by_priority_bucket(accepted_items),
        rejection_reasons=_rejection_reasons(latest_decisions.values()),
        top_recurring_risk_flags=_top_risk_flags(workspace.review_items),
        time_to_decision=_time_to_decision(workspace, latest_decisions),
        reviewer_activity_summary=_reviewer_activity(workspace),
        feedback_conflict_count=_feedback_conflict_count(workspace.decisions),
    )


def _latest_decisions_by_item(
    decisions: list[ReviewerDecision],
) -> dict[str, ReviewerDecision]:
    latest: dict[str, ReviewerDecision] = {}
    for decision in sorted(decisions, key=lambda item: item.created_at):
        latest[decision.review_item_id] = decision
    return latest


def _accepted_by_origin(items: list[ReviewItem]) -> dict[str, int]:
    counts = {"existing": 0, "generated": 0}
    counts.update(Counter(item.candidate_origin for item in items))
    return dict(sorted(counts.items()))


def _accepted_by_target(items: list[ReviewItem]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(item.target_symbols)
    return dict(sorted(counts.items()))


def _accepted_by_priority_bucket(items: list[ReviewItem]) -> dict[str, int]:
    return dict(sorted(Counter(item.priority_bucket for item in items).items()))


def _rejection_reasons(decisions: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for decision in decisions:
        if decision.decision not in {"reject", "deprioritize"}:
            continue
        if decision.decision_factors:
            counts.update(decision.decision_factors)
        else:
            counts["unspecified"] += 1
    return dict(sorted(counts.items()))


def _top_risk_flags(items: list[ReviewItem]) -> list[dict[str, int | str]]:
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(item.risk_flags)
    return [
        {"risk_flag": risk_flag, "count": count}
        for risk_flag, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _time_to_decision(
    workspace: ReviewWorkspace,
    latest_decisions: dict[str, ReviewerDecision],
) -> dict[str, Any]:
    durations: list[float] = []
    by_item: dict[str, float] = {}
    for review_item_id, decision in latest_decisions.items():
        duration = _duration_seconds(workspace.created_at, decision.created_at)
        if duration is None:
            continue
        durations.append(duration)
        by_item[review_item_id] = duration
    if not durations:
        return {"count": 0}
    return {
        "count": len(durations),
        "mean_seconds": round(sum(durations) / len(durations), 3),
        "min_seconds": round(min(durations), 3),
        "max_seconds": round(max(durations), 3),
        "by_review_item_id": dict(sorted(by_item.items())),
    }


def _duration_seconds(start: datetime, end: datetime) -> float | None:
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _reviewer_activity(workspace: ReviewWorkspace) -> dict[str, dict[str, Any]]:
    activity: dict[str, dict[str, Any]] = defaultdict(_empty_activity)
    for decision in workspace.decisions:
        reviewer_id = decision.reviewer.reviewer_id
        activity[reviewer_id]["decisions"] += 1
        activity[reviewer_id]["decision_counts"][decision.decision] = (
            activity[reviewer_id]["decision_counts"].get(decision.decision, 0) + 1
        )
        activity[reviewer_id]["latest_activity_at"] = _latest_iso(
            activity[reviewer_id]["latest_activity_at"],
            decision.created_at,
        )
    for comment in workspace.comments:
        reviewer_id = comment.reviewer.reviewer_id
        activity[reviewer_id]["comments"] += 1
        activity[reviewer_id]["latest_activity_at"] = _latest_iso(
            activity[reviewer_id]["latest_activity_at"],
            comment.created_at,
        )
    for request in workspace.followup_requests:
        reviewer_id = request.requested_by.reviewer_id
        activity[reviewer_id]["followup_requests"] += 1
        activity[reviewer_id]["latest_activity_at"] = _latest_iso(
            activity[reviewer_id]["latest_activity_at"],
            request.created_at,
        )
    return {reviewer_id: payload for reviewer_id, payload in sorted(activity.items())}


def _empty_activity() -> dict[str, Any]:
    return {
        "decisions": 0,
        "comments": 0,
        "followup_requests": 0,
        "decision_counts": {},
        "latest_activity_at": None,
    }


def _latest_iso(current: str | None, candidate: datetime) -> str:
    candidate_iso = candidate.isoformat()
    if current is None:
        return candidate_iso
    return max(current, candidate_iso)


def _feedback_conflict_count(decisions: list[ReviewerDecision]) -> int:
    decisions_by_item: dict[str, set[str]] = defaultdict(set)
    for decision in decisions:
        if decision.decision in POSITIVE_DECISIONS:
            decisions_by_item[decision.review_item_id].add("positive")
        elif decision.decision in NEGATIVE_DECISIONS:
            decisions_by_item[decision.review_item_id].add("negative")
        elif decision.decision in CAUTION_DECISIONS:
            decisions_by_item[decision.review_item_id].add("caution")
    return sum(
        1
        for signals in decisions_by_item.values()
        if "positive" in signals and bool(signals & {"negative", "caution"})
    )
