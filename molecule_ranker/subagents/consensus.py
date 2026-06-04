from __future__ import annotations

from typing import Any
from uuid import uuid4

from molecule_ranker.subagents.schemas import (
    SubagentConsensus,
    SubagentCritique,
    SubagentResult,
)


def synthesize_critique_consensus(
    *,
    parent_session_id: str,
    task_ids: list[str],
    results: list[SubagentResult],
    critiques: list[SubagentCritique],
    high_risk: bool = False,
) -> SubagentConsensus:
    failed_critiques = [critique for critique in critiques if not critique.passed]
    guardrail_failures = [
        critique
        for critique in failed_critiques
        if critique.critique_type == "scientific_guardrail"
        or critique.metadata.get("non_overridable") is True
    ]
    human_review_required = bool(failed_critiques and (high_risk or guardrail_failures))
    if failed_critiques and not human_review_required:
        human_review_required = True

    if failed_critiques:
        consensus_status = "requires_human_review"
        summary = "Unresolved critique disagreement requires human review."
    else:
        consensus_status = "agreed"
        summary = "Critique-and-revise workflow resolved without blocking disagreement."

    return SubagentConsensus(
        consensus_id=f"subagent-consensus-{uuid4().hex[:12]}",
        parent_session_id=parent_session_id,
        task_ids=list(dict.fromkeys(task_ids)),
        participating_subagent_ids=_participant_ids(results, critiques),
        consensus_status=consensus_status,
        summary=summary,
        agreements=_agreements(critiques),
        disagreements=[
            finding for critique in failed_critiques for finding in critique.findings
        ],
        recommended_next_actions=(
            ["Escalate residual disagreement to human reviewer."]
            if human_review_required
            else ["Use revised result with preserved version history."]
        ),
        human_review_required=human_review_required,
        metadata={
            "result_versions": [result.result_id for result in results],
            "failed_critique_ids": [critique.critique_id for critique in failed_critiques],
            "guardrail_failures_non_overridable": bool(guardrail_failures),
            "high_risk": high_risk,
        },
    )


def _participant_ids(
    results: list[SubagentResult],
    critiques: list[SubagentCritique],
) -> list[str]:
    return sorted(
        {
            *[result.subagent_id for result in results],
            *[critique.critic_subagent_id for critique in critiques],
        }
    )


def _agreements(critiques: list[SubagentCritique]) -> list[str]:
    if not critiques:
        return ["No critique findings were generated."]
    if all(critique.passed for critique in critiques):
        return ["All critique checks passed after revision."]
    return []


def consensus_metadata_summary(consensus: SubagentConsensus) -> dict[str, Any]:
    return {
        "consensus_id": consensus.consensus_id,
        "status": consensus.consensus_status,
        "human_review_required": consensus.human_review_required,
        "result_versions": consensus.metadata.get("result_versions", []),
    }


__all__ = [
    "consensus_metadata_summary",
    "synthesize_critique_consensus",
]
