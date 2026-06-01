from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from molecule_ranker.campaigns.schemas import CampaignPlan, ReplanTrigger


class ReplanningReport(BaseModel):
    campaign_id: str
    triggers: list[ReplanTrigger] = Field(default_factory=list)
    updated_plan: CampaignPlan
    rationale: str
    codex_may_summarize: bool = True
    codex_triggered_execution: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def evaluate_replanning(
    current_plan: CampaignPlan,
    *,
    new_events: Sequence[Mapping[str, Any]] = (),
    graph_updates: Sequence[Mapping[str, Any]] = (),
) -> ReplanningReport:
    triggers: list[ReplanTrigger] = []
    reprioritized: list[str] = []
    deprioritized: list[str] = []
    required_reviews: list[str] = []
    recommended_status: str | None = None
    rationale_parts: list[str] = []

    for event in new_events:
        event_triggers, effects = _triggers_for_event(current_plan.campaign_id, event)
        triggers.extend(event_triggers)
        reprioritized.extend(effects["reprioritized"])
        deprioritized.extend(effects["deprioritized"])
        required_reviews.extend(effects["required_reviews"])
        if effects.get("recommended_status") == "paused":
            recommended_status = "paused"
        rationale_parts.extend(effects["rationale"])

    for update in graph_updates:
        if _string(update.get("update_type")) == "contradiction_detected":
            triggers.append(
                _trigger(
                    current_plan.campaign_id,
                    "contradiction_detected",
                    "high",
                    "Graph update introduced a contradiction requiring deterministic replanning.",
                    _linked_entities(update),
                    "replan",
                    source_event_id=_optional_string(update.get("update_id")),
                )
            )
            rationale_parts.append("New graph contradiction can trigger campaign replanning.")

    if _budget_exceeded(current_plan) and not any(
        trigger.trigger_type == "budget_exceeded" for trigger in triggers
    ):
        triggers.append(
            _trigger(
                current_plan.campaign_id,
                "budget_exceeded",
                "high",
                "Campaign budget summary exceeds configured limits.",
                [],
                "pause",
            )
        )
        recommended_status = "paused"
        rationale_parts.append("Budget exceeded triggers pause/replan.")

    deduped = _dedupe_triggers(triggers)
    updated = current_plan.model_copy(
        update={
            "replan_triggers": [trigger.trigger_type for trigger in deduped],
            "metadata": {
                **current_plan.metadata,
                "reprioritized_hypothesis_ids": sorted(set(reprioritized)),
                "deprioritized_hypothesis_ids": sorted(set(deprioritized)),
                "required_reviews": sorted(set(required_reviews)),
                "recommended_campaign_status": recommended_status or "replanning_required",
                "deterministic_replanning": True,
                "codex_triggered_execution": False,
            },
            "warnings": [
                *current_plan.warnings,
                "Replanning report is deterministic; Codex may summarize rationale but cannot "
                "trigger automatic execution.",
            ],
        },
        deep=True,
    )
    return ReplanningReport(
        campaign_id=current_plan.campaign_id,
        triggers=deduped,
        updated_plan=updated,
        rationale=" ".join(rationale_parts)
        or "No deterministic replanning trigger was identified.",
        codex_may_summarize=True,
        codex_triggered_execution=False,
        metadata={
            "event_count": len(new_events),
            "graph_update_count": len(graph_updates),
        },
    )


def _triggers_for_event(
    campaign_id: str,
    event: Mapping[str, Any],
) -> tuple[list[ReplanTrigger], dict[str, Any]]:
    event_type = _string(event.get("event_type"))
    interpretation = _string(event.get("result_interpretation")).lower()
    status = _string(event.get("status")).lower()
    linked_entities = _linked_entities(event)
    source_event_id = _optional_string(event.get("event_id"))
    effects = {
        "reprioritized": [],
        "deprioritized": [],
        "required_reviews": [],
        "recommended_status": None,
        "rationale": [],
    }

    if event_type == "result_imported" and interpretation == "positive":
        hypothesis_id = _optional_string(event.get("hypothesis_id"))
        if hypothesis_id:
            effects["reprioritized"].append(hypothesis_id)
        effects["rationale"].append(
            "Positive result can reprioritize related hypotheses for deterministic review."
        )
        return [
            _trigger(
                campaign_id,
                "new_positive_result",
                "medium",
                "Imported positive result changed related campaign evidence context.",
                linked_entities,
                "replan",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "result_imported" and interpretation == "negative":
        hypothesis_id = _optional_string(event.get("hypothesis_id"))
        if hypothesis_id:
            effects["deprioritized"].append(hypothesis_id)
        effects["rationale"].append(
            "Negative result can deprioritize exact linked candidate or hypothesis."
        )
        return [
            _trigger(
                campaign_id,
                "new_negative_result",
                "medium",
                "Imported negative result changed exact linked candidate or hypothesis context.",
                linked_entities,
                "replan",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "result_imported" and interpretation == "safety_concern":
        effects["required_reviews"].append("safety_review_required")
        effects["recommended_status"] = "paused"
        effects["rationale"].append("Safety result triggers pause and expert review.")
        return [
            _trigger(
                campaign_id,
                "safety_concern",
                "critical",
                "Imported safety concern requires expert review before continuation.",
                linked_entities,
                "expert_review",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "result_imported" and interpretation == "failed_qc":
        effects["required_reviews"].append("more_data_review_required")
        effects["rationale"].append(
            "Failed QC triggers more-data/review and does not support a negative conclusion."
        )
        return [
            _trigger(
                campaign_id,
                "failed_qc",
                "medium",
                "Failed QC requires more-data/review and does not support a negative conclusion.",
                linked_entities,
                "expert_review",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "budget_changed":
        effects["recommended_status"] = "paused"
        effects["rationale"].append("Budget change can require pause/replan.")
        return [
            _trigger(
                campaign_id,
                "budget_exceeded",
                "high",
                "Budget change requires budget constraint review.",
                linked_entities,
                "pause",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "hypothesis_status_change" and status in {"stale", "retired"}:
        hypothesis_id = _optional_string(event.get("hypothesis_id"))
        if hypothesis_id:
            effects["deprioritized"].append(hypothesis_id)
        effects["rationale"].append("Stale or retired hypothesis triggers replanning review.")
        return [
            _trigger(
                campaign_id,
                "hypothesis_retired",
                "medium",
                "Hypothesis status changed to stale or retired.",
                linked_entities,
                "replan",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "contradiction_detected":
        effects["rationale"].append("New contradiction can trigger deterministic replanning.")
        return [
            _trigger(
                campaign_id,
                "contradiction_detected",
                "high",
                "Contradiction detection changed campaign assumptions.",
                linked_entities,
                "replan",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "model_retrained":
        effects["rationale"].append(
            "Model retraining can change uncertainty and campaign priority."
        )
        return [
            _trigger(
                campaign_id,
                "model_retrained",
                "low",
                "Model retraining changed campaign model context.",
                linked_entities,
                "continue",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "portfolio_reoptimization":
        effects["rationale"].append(
            "Portfolio reoptimization can change campaign candidate priority."
        )
        return [
            _trigger(
                campaign_id,
                "portfolio_changed",
                "medium",
                "Portfolio reoptimization changed campaign selection context.",
                linked_entities,
                "replan",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "integration_sync":
        effects["rationale"].append("Integration sync update can change imported campaign context.")
        return [
            _trigger(
                campaign_id,
                "external_sync_update",
                "low",
                "Integration sync changed campaign source artifacts.",
                linked_entities,
                "continue",
                source_event_id=source_event_id,
            )
        ], effects

    if event_type == "failed":
        effects["required_reviews"].append("failed_work_package_review_required")
        effects["rationale"].append("Failed work package requires review before continuation.")
        return [
            _trigger(
                campaign_id,
                "work_package_failed",
                "high",
                "Work package failure requires campaign review.",
                linked_entities,
                "expert_review",
                source_event_id=source_event_id,
            )
        ], effects

    return [], effects


def _trigger(
    campaign_id: str,
    trigger_type: str,
    severity: str,
    description: str,
    linked_entity_ids: list[str],
    recommended_action: str,
    *,
    source_event_id: str | None = None,
) -> ReplanTrigger:
    return ReplanTrigger(
        trigger_id=_stable_id("replan-trigger", campaign_id, trigger_type, source_event_id),
        campaign_id=campaign_id,
        trigger_type=trigger_type,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        description=description,
        linked_entity_ids=linked_entity_ids,
        recommended_action=recommended_action,  # type: ignore[arg-type]
        metadata={
            "source_event_id": source_event_id,
            "deterministic_trigger": True,
            "codex_triggered_execution": False,
        },
    )


def _linked_entities(event: Mapping[str, Any]) -> list[str]:
    output: list[str] = []
    for key in ("candidate_id", "hypothesis_id", "work_package_id", "entity_id"):
        value = _optional_string(event.get(key))
        if value:
            output.append(value)
    raw = event.get("linked_entity_ids")
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        output.extend(str(item) for item in raw if str(item))
    return sorted(set(output))


def _dedupe_triggers(triggers: Sequence[ReplanTrigger]) -> list[ReplanTrigger]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    output: list[ReplanTrigger] = []
    for trigger in triggers:
        key = (trigger.trigger_type, tuple(trigger.linked_entity_ids))
        if key in seen:
            continue
        seen.add(key)
        output.append(trigger)
    return output


def _budget_exceeded(plan: CampaignPlan) -> bool:
    return (
        plan.budget_summary.get("within_limits") is False
        or bool(plan.budget_summary.get("exceeded_dimensions"))
    )


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_string(value: Any) -> str | None:
    text = _string(value).strip()
    return text or None


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts if part is not None) or prefix
    return f"{prefix}:{uuid5(NAMESPACE_URL, raw).hex[:12]}"


__all__ = ["ReplanningReport", "evaluate_replanning"]
