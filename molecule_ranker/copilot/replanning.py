from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from molecule_ranker.copilot.schemas import (
    CoPilotAction,
    CoPilotActionResult,
    CoPilotTrigger,
)

ChangeLevel = Literal["informational", "minor", "major", "approval_required"]

_FORBIDDEN_TERMS = (
    "protocol",
    "synthesis",
    "synthesize",
    "dosing",
    "dose",
    "patient guidance",
    "wet-lab",
    "wet lab",
)


@dataclass(frozen=True)
class CampaignReplanContext:
    trigger: CoPilotTrigger
    campaign_state: dict[str, Any]
    plan_id: str | None
    affected_hypotheses: list[str]
    affected_work_packages: list[str]
    affected_candidates: list[str]


@dataclass(frozen=True)
class CampaignReplanDraft:
    draft_id: str
    campaign_id: str
    trigger_id: str
    trigger_rationale: str
    affected_hypotheses: list[str]
    affected_work_packages: list[str]
    affected_candidates: list[str]
    proposed_changes: list[str]
    budget_impact: str
    risks: list[str]
    approval_requirements: list[str]
    rollback_plan: list[str]
    limitations: list[str]
    change_level: ChangeLevel
    old_plan_id: str | None
    new_plan: dict[str, Any]
    codex_explanation: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


class CampaignReplanDraftWorkflow:
    def __init__(
        self,
        *,
        action_queue: Any | None = None,
        runtime_tool_registry: Any | None = None,
        codex_explainer: Callable[[CampaignReplanContext, CampaignReplanDraft], str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.action_queue = action_queue
        self.runtime_tool_registry = runtime_tool_registry
        self.codex_explainer = codex_explainer
        self._now = now or (lambda: datetime.now(UTC))

    def create_draft(
        self,
        trigger: CoPilotTrigger,
        *,
        campaign_state: dict[str, Any],
    ) -> CampaignReplanDraft:
        context = self.build_context(trigger, campaign_state=campaign_state)
        draft = self._deterministic_draft(context)
        if self.codex_explainer is not None:
            explanation = self.codex_explainer(context, draft)
            draft = self._with_codex_explanation(draft, explanation)
        self.validate_draft(draft)
        if draft.approval_requirements:
            self._queue_approval(draft)
        return draft

    def build_context(
        self,
        trigger: CoPilotTrigger,
        *,
        campaign_state: dict[str, Any],
    ) -> CampaignReplanContext:
        affected_candidates = self._ids_from_trigger_or_state(
            trigger,
            campaign_state,
            metadata_key="candidate_ids",
            state_key="candidates",
            id_key="candidate_id",
        )
        affected_hypotheses = self._ids_from_trigger_or_state(
            trigger,
            campaign_state,
            metadata_key="hypothesis_ids",
            state_key="hypotheses",
            id_key="hypothesis_id",
        )
        if not affected_hypotheses:
            affected_hypotheses = self._hypotheses_for_candidates(
                affected_candidates,
                campaign_state,
            )
        affected_work_packages = self._changed_work_packages(
            campaign_state,
            affected_hypotheses=affected_hypotheses,
            affected_candidates=affected_candidates,
        )
        plan_id = campaign_state.get("plan_id")
        return CampaignReplanContext(
            trigger=trigger,
            campaign_state=campaign_state,
            plan_id=str(plan_id) if plan_id is not None else None,
            affected_hypotheses=affected_hypotheses,
            affected_work_packages=affected_work_packages,
            affected_candidates=affected_candidates,
        )

    def validate_draft(self, draft: CampaignReplanDraft) -> None:
        payload = json.dumps(draft, default=str, sort_keys=True).lower()
        if any(term in payload for term in _FORBIDDEN_TERMS):
            raise ValueError("replan drafts must not contain protocol/synthesis/dosing details")
        if (
            "failed QC cannot support candidate rejection without review"
            in draft.limitations
            and any("reject" in change.lower() for change in draft.proposed_changes)
        ):
            raise ValueError("failed QC cannot cause candidate rejection without review")

    def compare_plans(
        self,
        *,
        old_plan: dict[str, Any],
        draft: CampaignReplanDraft,
    ) -> ChangeLevel:
        if draft.approval_requirements:
            return "approval_required"
        if old_plan == draft.new_plan:
            return "informational"
        if len(draft.affected_work_packages) > 1:
            return "major"
        return "minor"

    def apply_if_approved(
        self,
        draft: CampaignReplanDraft,
        *,
        approved: bool,
    ) -> CoPilotActionResult:
        action = self._run_replan_action(draft)
        if draft.approval_requirements and not approved:
            return CoPilotActionResult(
                result_id=f"result-{draft.draft_id}-approval-required",
                copilot_action_id=action.copilot_action_id,
                status="approval_required",
                artifact_ids=[],
                job_ids=[],
                summary="Human approval required before campaign replan execution.",
                warnings=[],
                created_at=self._now(),
                metadata={"draft_id": draft.draft_id},
            )
        if self.runtime_tool_registry is None:
            return CoPilotActionResult(
                result_id=f"result-{draft.draft_id}-missing-runtime",
                copilot_action_id=action.copilot_action_id,
                status="failed",
                artifact_ids=[],
                job_ids=[],
                summary="RuntimeToolRegistry is required to execute campaign replan.",
                warnings=[],
                created_at=self._now(),
                metadata={"draft_id": draft.draft_id},
            )
        tool_result = self.runtime_tool_registry.execute(
            "run_campaign_replan",
            {"draft_id": draft.draft_id, "campaign_id": draft.campaign_id},
        )
        return CoPilotActionResult(
            result_id=f"result-{draft.draft_id}-applied",
            copilot_action_id=action.copilot_action_id,
            status="succeeded",
            artifact_ids=self._string_list(tool_result.get("artifact_ids")),
            job_ids=self._string_list(tool_result.get("job_ids")),
            summary=str(tool_result.get("summary", "Campaign replan applied.")),
            warnings=[],
            created_at=self._now(),
            metadata={"draft_id": draft.draft_id},
        )

    def _deterministic_draft(
        self,
        context: CampaignReplanContext,
    ) -> CampaignReplanDraft:
        trigger = context.trigger
        detector_type = str(trigger.metadata.get("detector_event_type", trigger.trigger_type))
        proposed_changes: list[str] = []
        risks: list[str] = []
        limitations = ["Recommendations are planning aids and require source review."]
        approval_requirements: list[str] = []
        budget_impact = "No deterministic budget change proposed."
        rollback_plan = ["Restore prior campaign plan artifact if approved changes are reverted."]
        change_level: ChangeLevel = "informational"

        if detector_type == "positive_qc_passed_exact_assay_result":
            proposed_changes.append("record source-grounded follow-up")
            proposed_changes.append("prepare review request for result interpretation")
            budget_impact = "Possible low-cost review follow-up; no autonomous budget increase."
            change_level = "minor"
        elif detector_type == "failed_qc_result":
            proposed_changes.append("request QC review before interpreting result")
            limitations.append("failed QC cannot support candidate rejection without review")
            budget_impact = "No budget change until QC review resolves data usability."
            change_level = "informational"
        elif detector_type == "safety_developability_concern":
            proposed_changes.append("pause affected work package for safety/developability review")
            proposed_changes.append("create human review request for affected candidates")
            risks.append("Safety or developability concern may change campaign priority.")
            approval_requirements.append(
                "human approval required before changing active campaign plan"
            )
            budget_impact = "Potential delay or review cost; no autonomous spend committed."
            change_level = "approval_required"
        elif detector_type == "negative_qc_passed_exact_assay_result":
            proposed_changes.append("create hypothesis review request for negative exact result")
            proposed_changes.append("draft update to affected work package priorities")
            risks.append("Negative result may require hypothesis reprioritization after review.")
            budget_impact = "Potential reallocation after approval; no autonomous spend committed."
            change_level = "major"
        else:
            proposed_changes.append("refresh campaign plan notes for trigger context")

        if trigger.metadata.get("generated_molecule_assay_advancement"):
            proposed_changes.append("route generated molecule advancement through review gate")
            approval_requirements.append(
                "review gate required for generated molecule assay advancement"
            )
            change_level = "approval_required"

        if trigger.metadata.get("proposed_change_scope") == "major":
            change_level = "major"

        if change_level == "major" or (
            trigger.requires_human_attention
            and detector_type not in {"failed_qc_result"}
        ):
            approval_requirements = self._dedupe_strings(
                [
                    *approval_requirements,
                    "human approval required before changing active campaign plan",
                ]
            )
            change_level = "approval_required"

        new_plan = self._new_plan(context, proposed_changes=proposed_changes)
        return CampaignReplanDraft(
            draft_id=f"replan-draft-{trigger.trigger_id}",
            campaign_id=trigger.campaign_id,
            trigger_id=trigger.trigger_id,
            trigger_rationale=trigger.rationale,
            affected_hypotheses=context.affected_hypotheses,
            affected_work_packages=context.affected_work_packages,
            affected_candidates=context.affected_candidates,
            proposed_changes=proposed_changes,
            budget_impact=budget_impact,
            risks=risks,
            approval_requirements=approval_requirements,
            rollback_plan=rollback_plan,
            limitations=limitations,
            change_level=change_level,
            old_plan_id=context.plan_id,
            new_plan=new_plan,
            created_at=self._now(),
            metadata={
                "detector_event_type": detector_type,
                "trigger_signature": trigger.trigger_signature,
            },
        )

    def _with_codex_explanation(
        self,
        draft: CampaignReplanDraft,
        explanation: str,
    ) -> CampaignReplanDraft:
        explained = CampaignReplanDraft(
            draft_id=draft.draft_id,
            campaign_id=draft.campaign_id,
            trigger_id=draft.trigger_id,
            trigger_rationale=draft.trigger_rationale,
            affected_hypotheses=list(draft.affected_hypotheses),
            affected_work_packages=list(draft.affected_work_packages),
            affected_candidates=list(draft.affected_candidates),
            proposed_changes=list(draft.proposed_changes),
            budget_impact=draft.budget_impact,
            risks=list(draft.risks),
            approval_requirements=list(draft.approval_requirements),
            rollback_plan=list(draft.rollback_plan),
            limitations=list(draft.limitations),
            change_level=draft.change_level,
            old_plan_id=draft.old_plan_id,
            new_plan=dict(draft.new_plan),
            codex_explanation=explanation,
            created_at=draft.created_at,
            metadata=dict(draft.metadata),
        )
        if explained.proposed_changes != draft.proposed_changes:
            raise ValueError("Codex explanation cannot add unsupported changes")
        return explained

    def _queue_approval(self, draft: CampaignReplanDraft) -> None:
        if self.action_queue is None:
            return
        action = CoPilotAction(
            copilot_action_id=f"action-{draft.draft_id}-approval",
            campaign_id=draft.campaign_id,
            trigger_id=draft.trigger_id,
            action_type="request_approval",
            tool_name=None,
            tool_args={"draft_id": draft.draft_id, "campaign_id": draft.campaign_id},
            side_effect_level="db_write",
            risk_level="high",
            requires_approval=True,
            approval_reason="; ".join(draft.approval_requirements),
            status="queued",
            created_at=self._now(),
            completed_at=None,
            metadata={
                "dedupe_key": f"{draft.campaign_id}:{draft.trigger_id}:replan-approval",
                "change_level": draft.change_level,
            },
        )
        self.action_queue.queue_action(action)

    def _run_replan_action(self, draft: CampaignReplanDraft) -> CoPilotAction:
        return CoPilotAction(
            copilot_action_id=f"action-{draft.draft_id}-run-replan",
            campaign_id=draft.campaign_id,
            trigger_id=draft.trigger_id,
            action_type="run_campaign_replan",
            tool_name="run_campaign_replan",
            tool_args={"draft_id": draft.draft_id, "campaign_id": draft.campaign_id},
            side_effect_level="db_write",
            risk_level="high",
            requires_approval=bool(draft.approval_requirements),
            approval_reason=(
                "; ".join(draft.approval_requirements)
                if draft.approval_requirements
                else None
            ),
            status="approved" if draft.approval_requirements else "queued",
            created_at=self._now(),
            completed_at=None,
            metadata={"changes_active_plan": bool(draft.approval_requirements)},
        )

    def _new_plan(
        self,
        context: CampaignReplanContext,
        *,
        proposed_changes: list[str],
    ) -> dict[str, Any]:
        plan = dict(context.campaign_state.get("active_plan", {}))
        plan["plan_id"] = context.plan_id
        plan["pending_replan"] = {
            "trigger_id": context.trigger.trigger_id,
            "affected_work_packages": context.affected_work_packages,
            "proposed_changes": proposed_changes,
        }
        return plan

    def _ids_from_trigger_or_state(
        self,
        trigger: CoPilotTrigger,
        campaign_state: dict[str, Any],
        *,
        metadata_key: str,
        state_key: str,
        id_key: str,
    ) -> list[str]:
        metadata_ids = self._string_list(trigger.metadata.get(metadata_key))
        if metadata_ids:
            return metadata_ids
        return [
            str(record[id_key])
            for record in self._record_list(campaign_state.get(state_key))
            if id_key in record
        ]

    def _hypotheses_for_candidates(
        self,
        candidate_ids: list[str],
        campaign_state: dict[str, Any],
    ) -> list[str]:
        candidate_set = set(candidate_ids)
        hypotheses: list[str] = []
        for candidate in self._record_list(campaign_state.get("candidates")):
            candidate_id = candidate.get("candidate_id")
            hypothesis_id = candidate.get("hypothesis_id")
            if candidate_id in candidate_set and hypothesis_id is not None:
                hypotheses.append(str(hypothesis_id))
        return self._dedupe_strings(hypotheses)

    def _changed_work_packages(
        self,
        campaign_state: dict[str, Any],
        *,
        affected_hypotheses: list[str],
        affected_candidates: list[str],
    ) -> list[str]:
        hypothesis_set = set(affected_hypotheses)
        candidate_set = set(affected_candidates)
        changed: list[str] = []
        for work_package in self._record_list(campaign_state.get("work_packages")):
            package_id = work_package.get("work_package_id")
            if package_id is None:
                continue
            package_candidates = set(self._string_list(work_package.get("candidate_ids")))
            hypothesis_id = work_package.get("hypothesis_id")
            if (
                hypothesis_id in hypothesis_set
                or bool(package_candidates.intersection(candidate_set))
            ):
                changed.append(str(package_id))
        return self._dedupe_strings(changed)

    def _record_list(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))
