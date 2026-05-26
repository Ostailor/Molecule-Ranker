from __future__ import annotations

from molecule_ranker.review.audit import audit_event
from molecule_ranker.review.schemas import (
    FollowupRequest,
    Reviewer,
    ReviewerComment,
    ReviewerDecision,
    ReviewWorkspace,
)


class ReviewDecisionEngine:
    def record_decision(
        self,
        workspace: ReviewWorkspace,
        *,
        review_item_id: str,
        reviewer: Reviewer,
        decision: str,
        rationale: str,
        confidence: float,
        decision_factors: list[str] | None = None,
    ) -> ReviewerDecision:
        before = _item_status(workspace, review_item_id)
        review_decision = ReviewerDecision(
            review_item_id=review_item_id,
            reviewer=reviewer,
            decision=decision,  # type: ignore[arg-type]
            rationale=rationale,
            confidence=confidence,
            decision_factors=decision_factors or [],
        )
        workspace.decisions.append(review_decision)
        _set_item_status(workspace, review_item_id, decision)
        workspace.audit_events.append(
            audit_event(
                event_type="decision_recorded",
                actor=reviewer,
                object_type="ReviewerDecision",
                object_id=review_decision.decision_id,
                summary=f"Decision recorded: {decision}",
                before=before,
                after=review_decision.model_dump(mode="json"),
            )
        )
        return review_decision

    def add_comment(
        self,
        workspace: ReviewWorkspace,
        *,
        review_item_id: str,
        reviewer: Reviewer,
        comment_text: str,
        comment_type: str = "general",
    ) -> ReviewerComment:
        comment = ReviewerComment(
            review_item_id=review_item_id,
            reviewer=reviewer,
            comment_text=comment_text,
            comment_type=comment_type,  # type: ignore[arg-type]
        )
        workspace.comments.append(comment)
        workspace.audit_events.append(
            audit_event(
                event_type="comment_added",
                actor=reviewer,
                object_type="ReviewerComment",
                object_id=comment.comment_id,
                summary=f"Comment added: {comment_type}",
                after=comment.model_dump(mode="json"),
            )
        )
        return comment

    def request_followup(
        self,
        workspace: ReviewWorkspace,
        *,
        review_item_id: str,
        reviewer: Reviewer,
        request_type: str,
        request_text: str,
        priority: str = "medium",
    ) -> FollowupRequest:
        request = FollowupRequest(
            review_item_id=review_item_id,
            requested_by=reviewer,
            request_type=request_type,  # type: ignore[arg-type]
            request_text=request_text,
            priority=priority,  # type: ignore[arg-type]
            status="open",
        )
        workspace.followup_requests.append(request)
        workspace.audit_events.append(
            audit_event(
                event_type="followup_requested",
                actor=reviewer,
                object_type="FollowupRequest",
                object_id=request.request_id,
                summary=f"Follow-up requested: {request_type}",
                after=request.model_dump(mode="json"),
            )
        )
        return request


def _item_status(workspace: ReviewWorkspace, review_item_id: str) -> dict[str, str] | None:
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return {"review_status": item.review_status}
    return None


def _set_item_status(workspace: ReviewWorkspace, review_item_id: str, decision: str) -> None:
    status_map = {
        "accept_for_followup": "accepted",
        "deprioritize": "deprioritized",
        "reject": "rejected",
        "needs_more_data": "needs_more_data",
        "escalate_to_expert": "escalated",
        "hold": "pending",
    }
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            item.review_status = status_map.get(decision, item.review_status)  # type: ignore[assignment]
            return
    raise ValueError(f"Unknown review item: {review_item_id}")
