from __future__ import annotations

from collections.abc import Mapping, Sequence

from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    HypothesisLifecycleEvent,
    HypothesisSet,
    ResearchHypothesis,
    TestableResearchQuestion,
)


def summarize_hypothesis_set(hypotheses: HypothesisSet) -> dict[str, int | str]:
    return {
        "graph_id": hypotheses.graph_id,
        "schema_version": hypotheses.schema_version,
        "hypothesis_count": len(hypotheses.hypotheses),
    }


def render_hypothesis_report_markdown(
    hypotheses: Sequence[ResearchHypothesis],
    *,
    evidence_gaps_by_hypothesis: Mapping[str, Sequence[EvidenceGap]] | None = None,
    criteria_by_hypothesis: Mapping[str, Sequence[FalsificationCriterion]] | None = None,
    questions_by_hypothesis: Mapping[str, Sequence[TestableResearchQuestion]] | None = None,
    lifecycle_events: Sequence[HypothesisLifecycleEvent] | None = None,
) -> str:
    gaps = evidence_gaps_by_hypothesis or {}
    criteria = criteria_by_hypothesis or {}
    questions = questions_by_hypothesis or {}
    events = list(lifecycle_events or [])
    lines = [
        "# Hypothesis Report",
        "",
        "## Hypothesis Summary",
        "",
        "- Hypotheses are not evidence.",
        "- Questions are not protocols.",
        "- No synthesis instructions are provided.",
        "- No lab protocols are provided.",
        "- No dosing guidance is provided.",
        "- No clinical claims are made.",
        "- Generated molecules remain computational hypotheses.",
        "",
        f"Total hypotheses: {len(hypotheses)}",
        f"Generated-molecule hypotheses: {_count_type(hypotheses, 'generated_molecule')}",
        f"Evidence gaps: {sum(len(items) for items in gaps.values())}",
        f"Research questions: {sum(len(items) for items in questions.values())}",
        "",
        "## Top Hypotheses",
        "",
    ]
    for hypothesis in sorted(hypotheses, key=lambda item: -item.priority_score)[:10]:
        lines.extend(_hypothesis_bullets(hypothesis))
    lines.extend(["", "## Mechanistic Hypotheses", ""])
    _append_hypothesis_group(lines, hypotheses, {"mechanism", "disease_target"})
    lines.extend(["", "## Generated-Molecule Hypotheses", ""])
    generated = [
        hypothesis
        for hypothesis in hypotheses
        if hypothesis.hypothesis_type == "generated_molecule"
    ]
    _append_hypothesis_group(lines, generated, {"generated_molecule"})
    if generated:
        lines.append(
            "- Generated no-direct-evidence warning: exact generated molecules require "
            "direct linked evidence before follow-up planning."
        )
    lines.extend(["", "## Contradiction-Resolution Hypotheses", ""])
    _append_hypothesis_group(lines, hypotheses, {"assay_contradiction"})
    lines.extend(["", "## Evidence Gaps", ""])
    for hypothesis in hypotheses:
        hypothesis_gaps = gaps.get(hypothesis.hypothesis_id, [])
        if not hypothesis_gaps:
            continue
        lines.extend(
            [
                f"### {hypothesis.hypothesis_id}",
                "",
            ]
        )
        for gap in hypothesis_gaps:
            lines.append(
                f"- `{gap.severity}` `{gap.gap_type}`: {gap.description} "
                f"Resolution: {gap.suggested_high_level_resolution}"
            )
    if not any(gaps.values()):
        lines.append("- none")
    lines.extend(["", "## Falsification Criteria", ""])
    for hypothesis in hypotheses:
        hypothesis_criteria = criteria.get(hypothesis.hypothesis_id, [])
        if not hypothesis_criteria:
            continue
        lines.extend([f"### {hypothesis.hypothesis_id}", ""])
        for criterion in hypothesis_criteria:
            lines.append(
                f"- {criterion.criterion_text} Decision impact: `{criterion.decision_impact}`."
            )
    if not any(criteria.values()):
        lines.append("- none")
    lines.extend(["", "## Testable Research Questions", ""])
    for hypothesis in hypotheses:
        hypothesis_questions = questions.get(hypothesis.hypothesis_id, [])
        if not hypothesis_questions:
            continue
        lines.extend([f"### {hypothesis.hypothesis_id}", ""])
        for question in hypothesis_questions:
            lines.append(f"- {question.question_text}")
    if not any(questions.values()):
        lines.append("- none")
    lines.extend(["", "## Review Status", ""])
    for hypothesis in hypotheses:
        lines.append(
            f"- `{hypothesis.hypothesis_id}`: `{hypothesis.status}`; "
            f"review decisions: {len(hypothesis.review_decision_ids)}"
        )
    if events:
        lines.append("")
        lines.append("Lifecycle events:")
        for event in events:
            lines.append(
                f"- `{event.event_type}` for `{event.hypothesis_id}` by "
                f"`{event.actor or 'system'}`"
            )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Hypotheses are not evidence.",
            "- Research questions are planning questions, not execution instructions.",
            "- No synthesis instructions, lab protocols, dosing guidance, or clinical "
            "claims are provided.",
            "- Generated molecules remain computational hypotheses until exact imported "
            "evidence and human review support further planning.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _hypothesis_bullets(hypothesis: ResearchHypothesis) -> list[str]:
    return [
        f"- `{hypothesis.hypothesis_id}` `{hypothesis.hypothesis_type}` "
        f"priority {hypothesis.priority_score:.3f}: {hypothesis.title}",
        f"  Statement: {hypothesis.statement}",
    ]


def _append_hypothesis_group(
    lines: list[str],
    hypotheses: Sequence[ResearchHypothesis],
    types: set[str],
) -> None:
    selected = [hypothesis for hypothesis in hypotheses if hypothesis.hypothesis_type in types]
    if not selected:
        lines.append("- none")
        return
    for hypothesis in selected:
        lines.extend(_hypothesis_bullets(hypothesis))


def _count_type(hypotheses: Sequence[ResearchHypothesis], hypothesis_type: str) -> int:
    return sum(1 for hypothesis in hypotheses if hypothesis.hypothesis_type == hypothesis_type)


__all__ = ["render_hypothesis_report_markdown", "summarize_hypothesis_set"]
