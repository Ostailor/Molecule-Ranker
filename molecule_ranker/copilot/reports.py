from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.copilot.schemas import (
    CampaignEvent,
    CoPilotAction,
    CoPilotActionResult,
    CoPilotStatusUpdate,
)

StatusCadence = Literal[
    "manual",
    "daily",
    "weekly",
    "after important trigger",
    "after campaign replan",
]

_STATUS_JSON_ARTIFACT = "copilot_status_update.json"
_STATUS_MD_ARTIFACT = "copilot_status_update.md"
_FORBIDDEN_CLAIM_PATTERNS = (
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+active\b", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+safe\b", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+effective\b", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:is|was|are|were)\s+synthesizable\b", re.IGNORECASE),
    re.compile(r"\btherapeutic\b", re.IGNORECASE),
    re.compile(r"\bbinding\b", re.IGNORECASE),
    re.compile(r"\bproves?\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class CoPilotStatusReportBundle:
    update: CoPilotStatusUpdate
    artifacts: dict[str, str]


class CoPilotStatusReporter:
    def __init__(
        self,
        *,
        artifact_dir: Path | str | None = None,
        codex_drafter: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else None
        self.codex_drafter = codex_drafter
        self._now = now or (lambda: datetime.now(UTC))

    def build(
        self,
        *,
        campaign_id: str,
        period_start: datetime,
        period_end: datetime,
        events: list[CampaignEvent],
        actions: list[CoPilotAction],
    ) -> CoPilotStatusUpdate:
        return self.build_status_update(
            campaign_id=campaign_id,
            period_start=period_start,
            period_end=period_end,
            cadence="manual",
            events=events,
            actions=actions,
        ).update

    def build_status_update(
        self,
        *,
        campaign_id: str,
        period_start: datetime,
        period_end: datetime,
        cadence: StatusCadence,
        events: list[CampaignEvent],
        actions: list[CoPilotAction],
        action_results: list[CoPilotActionResult] | None = None,
        use_codex: bool = False,
    ) -> CoPilotStatusReportBundle:
        action_results = action_results or []
        deterministic = self._deterministic_update(
            campaign_id=campaign_id,
            period_start=period_start,
            period_end=period_end,
            cadence=cadence,
            events=events,
            actions=actions,
            action_results=action_results,
        )
        update = deterministic
        if use_codex and self.codex_drafter is not None:
            update = self._apply_codex_draft(
                deterministic,
                events=events,
                actions=actions,
                action_results=action_results,
            )
        update = self._sanitize_update(update)
        artifacts = self._render_artifacts(update)
        self._write_artifacts(artifacts)
        return CoPilotStatusReportBundle(update=update, artifacts=artifacts)

    def _deterministic_update(
        self,
        *,
        campaign_id: str,
        period_start: datetime,
        period_end: datetime,
        cadence: StatusCadence,
        events: list[CampaignEvent],
        actions: list[CoPilotAction],
        action_results: list[CoPilotActionResult],
    ) -> CoPilotStatusUpdate:
        key_events = [event.event_id for event in events]
        actions_taken = [
            action.copilot_action_id
            for action in actions
            if action.status == "succeeded"
            or self._result_for_action(action.copilot_action_id, action_results) is not None
        ]
        approvals_needed = [
            action.copilot_action_id
            for action in actions
            if action.requires_approval
            and action.status not in {"approved", "succeeded", "rejected", "skipped"}
        ]
        blockers = [
            event.event_id
            for event in events
            if event.severity == "critical"
            or event.event_type in {"guardrail_failure", "job_failed"}
        ]
        risks = [
            event.event_id
            for event in events
            if event.severity in {"high", "critical"} and event.event_id not in blockers
        ]
        next_recommended_actions = [
            action.action_type
            for action in actions
            if action.status in {"proposed", "queued"} and not action.requires_approval
        ]
        artifact_refs = self._artifact_refs(events, action_results)
        return CoPilotStatusUpdate(
            status_update_id=f"status-{campaign_id}-{int(period_end.timestamp())}",
            campaign_id=campaign_id,
            period_start=period_start,
            period_end=period_end,
            executive_summary=self._executive_summary(
                campaign_id=campaign_id,
                cadence=cadence,
                key_events=key_events,
                actions_taken=actions_taken,
                approvals_needed=approvals_needed,
                blockers=blockers,
            ),
            key_events=key_events,
            actions_taken=actions_taken,
            approvals_needed=approvals_needed,
            blockers=blockers,
            risks=risks,
            next_recommended_actions=next_recommended_actions,
            limitations=[
                "Planning aid only.",
                "No lab protocols, synthesis instructions, dosing, or patient guidance.",
                "No scientific claims are inferred from co-pilot status reporting.",
            ],
            created_at=self._now(),
            metadata={
                "cadence": cadence,
                "artifact_refs": artifact_refs,
                "result_ids": [result.result_id for result in action_results],
                "generated_artifacts": [_STATUS_JSON_ARTIFACT, _STATUS_MD_ARTIFACT],
                "codex_draft_used": False,
                "codex_draft_rejected": False,
            },
        )

    def _apply_codex_draft(
        self,
        deterministic: CoPilotStatusUpdate,
        *,
        events: list[CampaignEvent],
        actions: list[CoPilotAction],
        action_results: list[CoPilotActionResult],
    ) -> CoPilotStatusUpdate:
        allowed_event_ids = {event.event_id for event in events}
        allowed_action_ids = {action.copilot_action_id for action in actions}
        allowed_result_ids = {result.result_id for result in action_results}
        payload = {
            "campaign_id": deterministic.campaign_id,
            "period_start": deterministic.period_start.isoformat(),
            "period_end": deterministic.period_end.isoformat(),
            "event_ids": sorted(allowed_event_ids),
            "action_ids": sorted(allowed_action_ids),
            "result_ids": sorted(allowed_result_ids),
            "deterministic_update": deterministic.model_dump(mode="json"),
        }
        codex_drafter = self.codex_drafter
        if codex_drafter is None:
            return deterministic
        draft = codex_drafter(payload)
        if not self._codex_ids_supported(
            draft,
            allowed_event_ids=allowed_event_ids,
            allowed_action_ids=allowed_action_ids,
            allowed_result_ids=allowed_result_ids,
        ):
            return deterministic.model_copy(
                update={
                    "metadata": {
                        **deterministic.metadata,
                        "codex_draft_rejected": True,
                        "codex_draft_used": False,
                    }
                },
                deep=True,
            )
        return deterministic.model_copy(
            update={
                "executive_summary": str(
                    draft.get("executive_summary", deterministic.executive_summary)
                ),
                "metadata": {
                    **deterministic.metadata,
                    "codex_draft_used": True,
                    "codex_draft_rejected": False,
                },
            },
            deep=True,
        )

    def _codex_ids_supported(
        self,
        draft: dict[str, Any],
        *,
        allowed_event_ids: set[str],
        allowed_action_ids: set[str],
        allowed_result_ids: set[str],
    ) -> bool:
        draft_event_ids = self._string_set(draft.get("key_events"))
        draft_action_ids = self._string_set(draft.get("actions_taken"))
        draft_result_ids = self._string_set(draft.get("result_ids"))
        return (
            draft_event_ids.issubset(allowed_event_ids)
            and draft_action_ids.issubset(allowed_action_ids)
            and draft_result_ids.issubset(allowed_result_ids)
        )

    def _sanitize_update(self, update: CoPilotStatusUpdate) -> CoPilotStatusUpdate:
        sanitized = update.model_copy(
            update={
                "executive_summary": self._sanitize_claims(update.executive_summary),
                "limitations": [
                    self._sanitize_claims(limitation) for limitation in update.limitations
                ],
            },
            deep=True,
        )
        self._validate_no_forbidden_claims(sanitized)
        return sanitized

    def _render_artifacts(self, update: CoPilotStatusUpdate) -> dict[str, str]:
        json_text = json.dumps(update.model_dump(mode="json"), indent=2, sort_keys=True)
        md_text = self._render_markdown(update)
        self._validate_no_forbidden_claims_in_text(json_text)
        self._validate_no_forbidden_claims_in_text(md_text)
        return {
            _STATUS_JSON_ARTIFACT: json_text,
            _STATUS_MD_ARTIFACT: md_text,
        }

    def _render_markdown(self, update: CoPilotStatusUpdate) -> str:
        lines = [
            f"# Co-Pilot Status Update: {update.campaign_id}",
            "",
            update.executive_summary,
            "",
            f"- Status update ID: {update.status_update_id}",
            f"- Period start: {update.period_start.isoformat()}",
            f"- Period end: {update.period_end.isoformat()}",
            f"- Cadence: {update.metadata.get('cadence', 'manual')}",
            "",
            "## Key Events",
            *self._bullet_lines(update.key_events),
            "",
            "## Actions Taken",
            *self._bullet_lines(update.actions_taken),
            "",
            "## Approvals Needed",
            *self._approval_lines(update),
            "",
            "## Blockers",
            *self._bullet_lines(update.blockers),
            "",
            "## Risks",
            *self._bullet_lines(update.risks),
            "",
            "## Next Recommended Actions",
            *self._bullet_lines(update.next_recommended_actions),
            "",
            "## Artifact Refs",
            *self._bullet_lines(self._metadata_list(update, "artifact_refs")),
            "",
            "## Limitations",
            *self._bullet_lines(update.limitations),
            "",
        ]
        return "\n".join(lines)

    def _write_artifacts(self, artifacts: dict[str, str]) -> None:
        if self.artifact_dir is None:
            return
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in artifacts.items():
            (self.artifact_dir / filename).write_text(content + "\n")

    def _approval_lines(self, update: CoPilotStatusUpdate) -> list[str]:
        if not update.approvals_needed:
            return ["- None"]
        return [
            f"- {action_id}: Human approval required before risky or gated action."
            for action_id in update.approvals_needed
        ]

    def _executive_summary(
        self,
        *,
        campaign_id: str,
        cadence: StatusCadence,
        key_events: list[str],
        actions_taken: list[str],
        approvals_needed: list[str],
        blockers: list[str],
    ) -> str:
        cadence_label = cadence.capitalize()
        return (
            f"{cadence_label} co-pilot status for {campaign_id}: "
            f"{len(key_events)} key event, {len(actions_taken)} action taken, "
            f"{len(approvals_needed)} approvals needed, {len(blockers)} blockers."
        )

    def _artifact_refs(
        self,
        events: list[CampaignEvent],
        action_results: list[CoPilotActionResult],
    ) -> list[str]:
        refs: list[str] = []
        for event in events:
            refs.extend(event.artifact_ids)
        for result in action_results:
            refs.extend(result.artifact_ids)
        return list(dict.fromkeys(refs))

    def _result_for_action(
        self,
        action_id: str,
        action_results: list[CoPilotActionResult],
    ) -> CoPilotActionResult | None:
        for result in action_results:
            if result.copilot_action_id == action_id and result.status == "succeeded":
                return result
        return None

    def _bullet_lines(self, values: list[str]) -> list[str]:
        if not values:
            return ["- None"]
        return [f"- {value}" for value in values]

    def _metadata_list(self, update: CoPilotStatusUpdate, key: str) -> list[str]:
        value = update.metadata.get(key)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _string_set(self, value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item) for item in value}

    def _sanitize_claims(self, text: str) -> str:
        sanitized = text
        for pattern in _FORBIDDEN_CLAIM_PATTERNS:
            sanitized = pattern.sub("[UNSUPPORTED_CLAIM_REDACTED]", sanitized)
        return sanitized

    def _validate_no_forbidden_claims(self, update: CoPilotStatusUpdate) -> None:
        self._validate_no_forbidden_claims_in_text(str(update.model_dump(mode="json")))

    def _validate_no_forbidden_claims_in_text(self, text: str) -> None:
        for pattern in _FORBIDDEN_CLAIM_PATTERNS:
            if pattern.search(text):
                raise ValueError("status update contains unsupported scientific claim")
