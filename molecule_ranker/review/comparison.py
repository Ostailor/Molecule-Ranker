from __future__ import annotations

from typing import Any

from molecule_ranker.review.schemas import (
    REVIEW_LIMITATIONS,
    CandidateComparison,
    ReviewItem,
    ReviewWorkspace,
)


def build_candidate_comparison(
    workspace: ReviewWorkspace,
    review_item_ids: list[str],
    *,
    allow_auto_recommendation: bool = False,
) -> CandidateComparison:
    if len(review_item_ids) < 2:
        raise ValueError("At least two review items are required for comparison.")
    items = [_get_item(workspace, review_item_id) for review_item_id in review_item_ids]
    decisions_by_item = {
        item.review_item_id: [
            decision.decision
            for decision in workspace.decisions
            if decision.review_item_id == item.review_item_id
        ]
        for item in items
    }
    table = [_comparison_row(item, decisions_by_item[item.review_item_id]) for item in items]
    shared_targets = _shared_targets(items)
    unique_targets = _unique_targets(items, shared_targets)
    differentiators = _differentiators(items, table)
    return CandidateComparison(
        candidates=[
            {
                "review_item_id": item.review_item_id,
                "candidate_id": item.candidate_id,
                "candidate_name": item.candidate_name,
                "candidate_origin": item.candidate_origin,
            }
            for item in items
        ],
        comparison_table=table,
        differentiators=differentiators,
        shared_targets=shared_targets,
        unique_targets=unique_targets,
        evidence_strength_comparison=_evidence_strength_comparison(items),
        literature_comparison=_literature_comparison(items),
        developability_comparison=_developability_comparison(items),
        generation_comparison=_generation_comparison(items),
        risk_comparison=_risk_comparison(items),
        recommendation_summary=_recommendation_summary(
            items,
            allow_auto_recommendation=allow_auto_recommendation,
        ),
        limitations=[
            "This is a comparison for expert review, not a clinical conclusion.",
            "No automatic winner is selected unless explicitly allowed by configuration.",
            *REVIEW_LIMITATIONS,
        ],
        metadata={
            "workspace_id": workspace.workspace_id,
            "run_id": workspace.run_id,
            "allow_auto_recommendation": allow_auto_recommendation,
        },
    )


def render_comparison_markdown(comparison: CandidateComparison) -> str:
    lines = [
        "# Candidate Comparison",
        "",
        comparison.recommendation_summary,
        "",
        f"Shared targets: {', '.join(comparison.shared_targets) or 'none'}",
        "",
        "## Comparison table",
        "",
        "| Candidate | Origin | Score | Confidence | Targets | Direct evidence | "
        "Literature support | Safety warnings | Developability risk | Reviewer decisions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in comparison.comparison_table:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["candidate_name"]),
                    str(row["candidate_origin"]),
                    str(row["score"]),
                    str(row["confidence"]),
                    ", ".join(row["target_symbols"]) or "none",
                    str(row["direct_evidence_count"]),
                    str(row["literature_support"]),
                    str(row["safety_warning_count"]),
                    str(row["developability_risk"]),
                    ", ".join(row["reviewer_decisions"]) or "none",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Differentiators", ""])
    lines.extend(f"- {item}" for item in comparison.differentiators)
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in comparison.limitations)
    return "\n".join(lines).rstrip() + "\n"


def _comparison_row(item: ReviewItem, reviewer_decisions: list[str]) -> dict[str, Any]:
    literature_counts = _literature_counts(item)
    direct_evidence_count = int(item.evidence_summary.get("molecule_evidence_count") or 0)
    generated_no_direct = (
        item.candidate_origin == "generated"
        or "generated_no_direct_evidence" in item.risk_flags
    )
    return {
        "review_item_id": item.review_item_id,
        "candidate_name": item.candidate_name,
        "candidate_origin": item.candidate_origin,
        "score": item.score,
        "confidence": item.confidence,
        "target_symbols": list(item.target_symbols),
        "target_overlap": list(item.target_symbols),
        "direct_evidence_count": direct_evidence_count,
        "literature_support": int(literature_counts.get("supports", 0) or 0),
        "literature_contradictions": int(literature_counts.get("contradicts", 0) or 0),
        "safety_warning_count": int(item.evidence_summary.get("safety_warning_count") or 0),
        "developability_risk": _developability_risk(item),
        "generated_no_direct_evidence": generated_no_direct,
        "reviewer_decisions": reviewer_decisions,
        "key_pros": _key_pros(item),
        "key_cons": _key_cons(item),
    }


def _key_pros(item: ReviewItem) -> list[str]:
    pros: list[str] = []
    if item.score is not None and item.score >= 0.7:
        pros.append("higher model score")
    if item.confidence is not None and item.confidence >= 0.7:
        pros.append("higher model confidence")
    if int(item.evidence_summary.get("molecule_evidence_count") or 0) > 0:
        pros.append("direct molecule-target evidence records present")
    if int(_literature_counts(item).get("supports", 0) or 0) > 0:
        pros.append("supporting literature claim metadata present")
    if _developability_risk(item) == "low":
        pros.append("lower developability risk annotation")
    return pros or ["No major differentiating pro identified."]


def _key_cons(item: ReviewItem) -> list[str]:
    cons: list[str] = []
    if item.candidate_origin == "generated":
        cons.append("generated hypothesis with no direct experimental evidence")
    if int(item.evidence_summary.get("molecule_evidence_count") or 0) == 0:
        cons.append("no direct molecule-target evidence records")
    if int(_literature_counts(item).get("contradicts", 0) or 0) > 0:
        cons.append("contradictory literature claim metadata present")
    if int(item.evidence_summary.get("safety_warning_count") or 0) > 0:
        cons.append("safety warning metadata present")
    risk = _developability_risk(item)
    if risk in {"medium", "high", "critical", "unknown"}:
        cons.append(f"developability risk annotation is {risk}")
    if item.risk_flags:
        cons.append(f"risk flags: {', '.join(item.risk_flags)}")
    return cons or ["No major differentiating con identified."]


def _differentiators(items: list[ReviewItem], table: list[dict[str, Any]]) -> list[str]:
    differentiators: list[str] = []
    scores = [item.score for item in items if item.score is not None]
    if len(set(scores)) > 1:
        best = max(items, key=lambda item: item.score or -1.0)
        differentiators.append(f"{best.candidate_name} has the highest model score.")
    direct_counts = {row["candidate_name"]: row["direct_evidence_count"] for row in table}
    if len(set(direct_counts.values())) > 1:
        differentiators.append(f"Direct evidence counts differ: {direct_counts}.")
    for row in table:
        if row["generated_no_direct_evidence"]:
            differentiators.append(
                f"{row['candidate_name']} is generated and has no direct experimental evidence."
            )
    risk_levels = {row["candidate_name"]: row["developability_risk"] for row in table}
    if len(set(risk_levels.values())) > 1:
        differentiators.append(f"Developability risk annotations differ: {risk_levels}.")
    return differentiators or ["No major differentiators were identified."]


def _shared_targets(items: list[ReviewItem]) -> list[str]:
    target_sets = [set(item.target_symbols) for item in items]
    if not target_sets:
        return []
    return sorted(set.intersection(*target_sets))


def _unique_targets(items: list[ReviewItem], shared_targets: list[str]) -> dict[str, list[str]]:
    shared = set(shared_targets)
    return {
        item.candidate_name: sorted(set(item.target_symbols) - shared)
        for item in items
    }


def _evidence_strength_comparison(items: list[ReviewItem]) -> dict[str, Any]:
    return {
        item.candidate_name: {
            "score": item.score,
            "confidence": item.confidence,
            "direct_evidence_count": item.evidence_summary.get("molecule_evidence_count"),
            "target_evidence_count": item.evidence_summary.get("target_evidence_count"),
        }
        for item in items
    }


def _literature_comparison(items: list[ReviewItem]) -> dict[str, Any]:
    return {item.candidate_name: _literature_counts(item) for item in items}


def _developability_comparison(items: list[ReviewItem]) -> dict[str, Any]:
    return {
        item.candidate_name: {
            "risk": _developability_risk(item),
            "summary": item.developability_summary,
        }
        for item in items
    }


def _generation_comparison(items: list[ReviewItem]) -> dict[str, Any]:
    return {
        item.candidate_name: {
            "candidate_origin": item.candidate_origin,
            "generated_no_direct_evidence": item.candidate_origin == "generated",
            "generation_summary": item.generation_summary,
        }
        for item in items
    }


def _risk_comparison(items: list[ReviewItem]) -> dict[str, Any]:
    return {
        item.candidate_name: {
            "risk_flags": item.risk_flags,
            "warnings": item.warnings,
            "safety_warning_count": item.evidence_summary.get("safety_warning_count"),
        }
        for item in items
    }


def _recommendation_summary(
    items: list[ReviewItem],
    *,
    allow_auto_recommendation: bool,
) -> str:
    if not allow_auto_recommendation:
        return (
            "This is a comparison for expert review. No automatic winner is selected; "
            "human reviewers should interpret model scores, evidence, warnings, and "
            "limitations separately from explicit review decisions."
        )
    top = max(items, key=lambda item: item.score or 0.0)
    return (
        f"Configuration allows an automated comparison note: {top.candidate_name} has "
        "the highest model score. This remains a triage suggestion for expert review."
    )


def _literature_counts(item: ReviewItem) -> dict[str, int]:
    raw = (
        item.evidence_summary.get("literature_claim_counts")
        or item.literature_summary.get("claim_counts")
        or {}
    )
    if not isinstance(raw, dict):
        return {"supports": 0, "contradicts": 0, "mentions": 0}
    return {
        "supports": int(raw.get("supports", 0) or 0),
        "contradicts": int(raw.get("contradicts", 0) or 0),
        "mentions": int(raw.get("mentions", 0) or 0),
    }


def _developability_risk(item: ReviewItem) -> str:
    return str(
        item.evidence_summary.get("developability_risk_level")
        or item.developability_summary.get("risk_level")
        or "unknown"
    )


def _get_item(workspace: ReviewWorkspace, review_item_id: str) -> ReviewItem:
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return item
    raise ValueError(f"Unknown review item: {review_item_id}")
