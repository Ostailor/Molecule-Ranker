"""Review-workflow integration for imported experimental assay results."""

from __future__ import annotations

from collections import Counter
from typing import Any, cast

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.review.audit import audit_event
from molecule_ranker.review.schemas import ReviewItem, ReviewStatus, ReviewWorkspace

EXPERIMENTAL_BOUNDARY_NOTE = (
    "Imported experimental results remain separate from reviewer decisions and do not "
    "establish clinical efficacy, safety, cure, or treatment."
)


def apply_experimental_results_to_review_workspace(
    workspace: ReviewWorkspace,
    results: list[AssayResult],
    *,
    config: dict[str, Any] | None = None,
) -> ReviewWorkspace:
    """Attach linked assay result summaries and conservative review-status suggestions."""

    config = config or {}
    grouped = {
        item.review_item_id: _results_for_review_item(item, results)
        for item in workspace.review_items
    }
    updated_items: list[ReviewItem] = []
    for item in workspace.review_items:
        item_results = grouped.get(item.review_item_id, [])
        if not item_results:
            updated_items.append(item)
            continue
        updated = attach_experimental_results_to_review_item(
            item,
            item_results,
            config=config,
        )
        updated_items.append(updated)
        workspace.audit_events.append(
            audit_event(
                event_type="experimental_results_linked_to_review_item",
                actor=str(config.get("actor") or "ExperimentalResultStore"),
                object_type="ReviewItem",
                object_id=item.review_item_id,
                summary=(
                    f"Linked {len(item_results)} imported experimental result(s) "
                    "to review item."
                ),
                before={
                    "review_status": item.review_status,
                    "experimental_result_count": _experimental_summary(item).get(
                        "result_count",
                        0,
                    ),
                },
                after={
                    "review_status": updated.review_status,
                    "experimental_result_count": len(item_results),
                    "suggestion": updated.metadata.get("experimental_review_suggestion"),
                },
                metadata={"result_ids": [result.result_id for result in item_results]},
            )
        )
    workspace.review_items = updated_items
    workspace.metadata["experimental_evidence_boundary"] = EXPERIMENTAL_BOUNDARY_NOTE
    return workspace


def attach_experimental_results_to_review_item(
    item: ReviewItem,
    results: list[AssayResult],
    *,
    config: dict[str, Any] | None = None,
) -> ReviewItem:
    config = config or {}
    summary = summarize_review_experimental_results(results)
    suggestion = _review_suggestion(summary, config=config)
    evidence_summary = {
        **item.evidence_summary,
        "experimental_results": summary,
    }
    metadata = {
        **item.metadata,
        "experimental_results": summary,
        "experimental_review_suggestion": suggestion,
        "experimental_evidence_boundary": EXPERIMENTAL_BOUNDARY_NOTE,
    }
    risk_flag_values = list(item.risk_flags)
    if summary["safety_concern_count"] > 0:
        risk_flag_values.append("experimental_safety_concern")
    risk_flags = sorted(set(risk_flag_values))
    warning_values = [
        *item.warnings,
        "Imported experimental results require expert interpretation.",
    ]
    if summary["failed_qc_count"] > 0:
        warning_values.append("Failed-QC imported assay results require result QC review.")
    warnings = sorted(set(warning_values))
    return item.model_copy(
        update={
            "evidence_summary": evidence_summary,
            "metadata": metadata,
            "risk_flags": risk_flags,
            "warnings": warnings,
            "review_status": suggestion["suggested_review_status"],
        }
    )


def summarize_review_experimental_results(results: list[AssayResult]) -> dict[str, Any]:
    counts = Counter(result.outcome_label for result in results)
    safety_results = [
        result
        for result in results
        if result.activity_direction in {"toxic", "worsened"}
        or result.assay_context.endpoint.endpoint_category == "safety"
        and result.activity_direction == "toxic"
    ]
    return {
        "result_count": len(results),
        "positive_count": counts.get("positive", 0),
        "negative_count": counts.get("negative", 0),
        "inconclusive_count": counts.get("inconclusive", 0),
        "failed_qc_count": counts.get("failed_qc", 0),
        "safety_concern_count": len(safety_results),
        "results": [_result_summary(result) for result in results],
        "boundary_note": EXPERIMENTAL_BOUNDARY_NOTE,
    }


def _review_suggestion(
    summary: dict[str, Any],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    if int(summary["safety_concern_count"]) > 0:
        return _suggestion(
            review_status="escalated",
            decision="escalate_to_expert",
            followup_type="safety_followup_review",
            rationale="Imported safety/toxicity assay outcome requires expert escalation.",
        )
    if int(summary["failed_qc_count"]) > 0 and int(summary["result_count"]) == int(
        summary["failed_qc_count"]
    ):
        return _suggestion(
            review_status="needs_more_data",
            decision="needs_more_data",
            followup_type="result_qc_review",
            rationale="Imported assay result failed QC and should not change evidence support.",
        )
    if int(summary["negative_count"]) > 0:
        return _suggestion(
            review_status="deprioritized",
            decision="deprioritize",
            followup_type="orthogonal_validation_review",
            rationale="Imported negative assay outcome lowers prioritization.",
        )
    if int(summary["positive_count"]) > 0:
        if bool(config.get("allow_experimental_auto_accept", False)):
            return _suggestion(
                review_status="accepted",
                decision="accept_for_followup",
                followup_type="orthogonal_validation_review",
                rationale=(
                    "Imported positive assay outcome supports follow-up, while remaining "
                    "separate from reviewer decisions."
                ),
            )
        return _suggestion(
            review_status="needs_expert_review",
            decision="accept_for_followup",
            followup_type="orthogonal_validation_review",
            rationale=(
                "Imported positive assay outcome suggests expert follow-up review; "
                "auto-accept is disabled."
            ),
        )
    return _suggestion(
        review_status="needs_more_data",
        decision="needs_more_data",
        followup_type="repeat_assay_review",
        rationale="Imported assay outcomes are inconclusive or incomplete.",
    )


def _suggestion(
    *,
    review_status: str,
    decision: str,
    followup_type: str,
    rationale: str,
) -> dict[str, Any]:
    return {
        "suggested_review_status": _review_status(review_status),
        "suggested_decision": decision,
        "suggested_followup_type": followup_type,
        "rationale": rationale,
        "boundary_note": EXPERIMENTAL_BOUNDARY_NOTE,
        "auto_generated": True,
    }


def _result_summary(result: AssayResult) -> dict[str, Any]:
    endpoint = result.assay_context.endpoint
    return {
        "result_id": result.result_id,
        "source_record_id": result.source_record_id,
        "candidate_id": result.candidate_id,
        "candidate_name": result.candidate_name,
        "assay_name": result.assay_context.assay_name,
        "assay_type": result.assay_context.assay_type,
        "endpoint_name": endpoint.name,
        "endpoint_category": endpoint.endpoint_category,
        "outcome_label": result.outcome_label,
        "activity_direction": result.activity_direction,
        "qc_status": result.qc_status,
        "normalized_value": result.normalized_value,
        "normalized_unit": result.normalized_unit,
        "confidence": result.confidence,
        "result_date": result.result_date.isoformat() if result.result_date else None,
        "source": result.source,
    }


def _results_for_review_item(
    item: ReviewItem,
    results: list[AssayResult],
) -> list[AssayResult]:
    matches: list[AssayResult] = []
    for result in results:
        if result.review_item_id and result.review_item_id == item.review_item_id:
            matches.append(result)
            continue
        if result.candidate_id and result.candidate_id == item.candidate_id:
            matches.append(result)
            continue
        if result.canonical_smiles and result.canonical_smiles == item.canonical_smiles:
            matches.append(result)
            continue
        if result.candidate_name.strip().lower() == item.candidate_name.strip().lower():
            matches.append(result)
    return matches


def _experimental_summary(item: ReviewItem) -> dict[str, Any]:
    summary = item.evidence_summary.get("experimental_results")
    return summary if isinstance(summary, dict) else {}


def _review_status(value: str) -> ReviewStatus:
    return cast(ReviewStatus, value)


__all__ = [
    "EXPERIMENTAL_BOUNDARY_NOTE",
    "apply_experimental_results_to_review_workspace",
    "attach_experimental_results_to_review_item",
    "summarize_review_experimental_results",
]
