from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.policies import AUTONOMY_ORDER
from molecule_ranker.agent_governance.schemas import (
    AgentCertification,
    AgentCertificationType,
    AgentGovernanceAutonomyLevel,
    AgentGovernanceSchema,
    AgentIncident,
)

CertificationActorType = Literal["human", "admin", "service_account", "codex", "agent"]
CertificationAuditAction = Literal[
    "created",
    "blocked",
    "checked",
    "expired",
    "revoked",
    "recertification_required",
]
CertificationChangeType = Literal[
    "tool_package_change",
    "prompt_template_change",
    "guardrail_change",
    "model_version_change",
    "major_policy_change",
]

DEFAULT_CERTIFICATION_STORE_PATH = Path(
    ".molecule-ranker/agent-governance/certifications.json"
)
AUTHORIZED_CERTIFICATION_ACTORS = {"human", "admin", "service_account"}
CODEX_ACTOR_IDS = {"codex", "codex_cli", "codex-runtime-agent", "codex_worker"}
HIGH_INCIDENT_SEVERITIES = {"high", "critical"}
DEFAULT_CERTIFICATION_VALIDITY_DAYS = 90
DEFAULT_UNCERTIFIED_AUTONOMY_CAP: AgentGovernanceAutonomyLevel = "suggest_only"


class AgentCertificationError(ValueError):
    """Raised when certification state cannot be modified as requested."""


class CertificationEvaluationResult(BaseModel):
    suite_name: str
    passed: bool
    score: float = Field(ge=0, le=1)
    artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CertificationChangeEvent(AgentGovernanceSchema):
    change_id: str
    agent_id: str
    change_type: CertificationChangeType
    changed_at: datetime
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCertificationAuditEvent(AgentGovernanceSchema):
    audit_event_id: str
    certification_id: str | None
    action: CertificationAuditAction
    actor_id: str
    occurred_at: datetime
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCertificationAuthorization(BaseModel):
    actor_id: str
    actor_type: CertificationActorType
    permission_scope: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def can_certify(self, certification_type: AgentCertificationType) -> bool:
        return "*" in self.permission_scope or certification_type in self.permission_scope


class AgentCertificationDecision(BaseModel):
    allowed: bool
    certification: AgentCertification | None = None
    reason: str
    audit_event: AgentCertificationAuditEvent
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAutonomyCertificationDecision(BaseModel):
    allowed: bool
    requested_autonomy_level: AgentGovernanceAutonomyLevel
    effective_autonomy_cap: AgentGovernanceAutonomyLevel
    certification: AgentCertification | None = None
    reason: str
    requires_recertification: bool
    recertification_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CertificationRecertificationDecision(BaseModel):
    required: bool
    reasons: list[str] = Field(default_factory=list)
    certification: AgentCertification | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCertificationStore:
    def __init__(self, path: Path | str = DEFAULT_CERTIFICATION_STORE_PATH) -> None:
        self.path = Path(path)

    def list_certifications(self) -> list[AgentCertification]:
        return [
            AgentCertification.model_validate(item)
            for item in self._load().get("certifications", [])
            if isinstance(item, dict)
        ]

    def list_audit_events(self) -> list[AgentCertificationAuditEvent]:
        return [
            AgentCertificationAuditEvent.model_validate(item)
            for item in self._load().get("audit_events", [])
            if isinstance(item, dict)
        ]

    def save_certifications(
        self,
        certifications: list[AgentCertification],
        audit_events: list[AgentCertificationAuditEvent],
    ) -> None:
        self._save(
            {
                "certifications": [
                    certification.model_dump(mode="json")
                    for certification in certifications
                ],
                "audit_events": [event.model_dump(mode="json") for event in audit_events],
            }
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"certifications": [], "audit_events": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"certifications": [], "audit_events": []}
        raw.setdefault("certifications", [])
        raw.setdefault("audit_events", [])
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class AgentCertificationManager:
    def __init__(
        self,
        *,
        certifications: list[AgentCertification] | None = None,
        audit_events: list[AgentCertificationAuditEvent] | None = None,
        store: AgentCertificationStore | None = None,
        eval_suite_runner: Callable[
            [str, AgentCertificationType],
            list[CertificationEvaluationResult],
        ]
        | None = None,
        default_validity_days: int = DEFAULT_CERTIFICATION_VALIDITY_DAYS,
    ) -> None:
        self.store = store
        if store is not None:
            self.certifications = store.list_certifications()
            self.audit_events = store.list_audit_events()
        else:
            self.certifications = list(certifications or [])
            self.audit_events = list(audit_events or [])
        self.eval_suite_runner = eval_suite_runner
        self.default_validity_days = default_validity_days

    def certify_agent(
        self,
        *,
        agent_id: str,
        certification_type: AgentCertificationType,
        certified_autonomy_level: AgentGovernanceAutonomyLevel,
        authorization: AgentCertificationAuthorization,
        evaluation_results: list[CertificationEvaluationResult] | None = None,
        guardrail_benchmark_passed: bool = True,
        role_tests_passed: bool = True,
        policy_compliant: bool = True,
        recent_incidents: list[AgentIncident] | None = None,
        certified_at: datetime | None = None,
        expires_at: datetime | None = None,
        limitations: list[str] | None = None,
        certification_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentCertificationDecision:
        now = certified_at or datetime.now(UTC)
        if evaluation_results is None and self.eval_suite_runner is not None:
            evaluation_results = self.eval_suite_runner(agent_id, certification_type)
        evaluation_results = list(evaluation_results or [])
        recent_incidents = list(recent_incidents or [])
        blocked_reason = _certification_block_reason(
            agent_id=agent_id,
            certification_type=certification_type,
            authorization=authorization,
            evaluation_results=evaluation_results,
            guardrail_benchmark_passed=guardrail_benchmark_passed,
            role_tests_passed=role_tests_passed,
            policy_compliant=policy_compliant,
            recent_incidents=recent_incidents,
        )
        if blocked_reason is not None:
            event = self._audit(
                certification_id=None,
                action="blocked",
                actor_id=authorization.actor_id,
                summary=blocked_reason,
                metadata={
                    "agent_id": agent_id,
                    "certification_type": certification_type,
                    "certified_autonomy_level": certified_autonomy_level,
                },
            )
            return AgentCertificationDecision(
                allowed=False,
                certification=None,
                reason=blocked_reason,
                audit_event=event,
            )

        certification = AgentCertification(
            certification_id=(
                certification_id or f"agent-certification-{uuid4().hex[:12]}"
            ),
            agent_id=agent_id,
            certification_type=certification_type,
            certified_autonomy_level=certified_autonomy_level,
            evaluation_artifact_ids=[
                result.artifact_id
                for result in evaluation_results
                if result.artifact_id is not None
            ],
            passed=True,
            score=_certification_score(evaluation_results),
            certified_by=authorization.actor_id,
            certified_at=now,
            expires_at=expires_at
            or now + timedelta(days=self.default_validity_days),
            limitations=limitations or [],
            metadata={
                **(metadata or {}),
                "authorized_actor_type": authorization.actor_type,
                "guardrail_benchmark_passed": guardrail_benchmark_passed,
                "role_tests_passed": role_tests_passed,
                "policy_compliant": policy_compliant,
                "evaluation_results": [
                    result.model_dump(mode="json") for result in evaluation_results
                ],
            },
        )
        self.certifications = [
            item
            for item in self.certifications
            if item.certification_id != certification.certification_id
        ]
        self.certifications.append(certification)
        event = self._audit(
            certification_id=certification.certification_id,
            action="created",
            actor_id=authorization.actor_id,
            summary=f"Created agent certification {certification.certification_id}.",
            metadata=certification.model_dump(mode="json"),
        )
        self._persist()
        return AgentCertificationDecision(
            allowed=True,
            certification=certification,
            reason="Agent certification created.",
            audit_event=event,
        )

    def revoke_certification(
        self,
        certification_id: str,
        *,
        revoked_by: str,
        revoked_at: datetime | None = None,
        reason: str = "Agent certification revoked.",
    ) -> AgentCertification:
        certification = self._require_certification(certification_id)
        updated = certification.model_copy(
            update={
                "metadata": {
                    **certification.metadata,
                    "revoked": True,
                    "revoked_by": revoked_by,
                    "revoked_at": (revoked_at or datetime.now(UTC)).isoformat(),
                    "revocation_reason": reason,
                }
            }
        )
        self.certifications = [
            updated if item.certification_id == certification_id else item
            for item in self.certifications
        ]
        self._audit(
            certification_id=certification_id,
            action="revoked",
            actor_id=revoked_by,
            summary=reason,
            metadata=updated.model_dump(mode="json"),
        )
        self._persist()
        return updated

    def list_certifications(
        self,
        *,
        agent_id: str | None = None,
        certification_type: AgentCertificationType | None = None,
        include_inactive: bool = True,
        now: datetime | None = None,
    ) -> list[AgentCertification]:
        current_time = now or datetime.now(UTC)
        certifications = self.certifications
        if agent_id is not None:
            certifications = [item for item in certifications if item.agent_id == agent_id]
        if certification_type is not None:
            certifications = [
                item
                for item in certifications
                if item.certification_type == certification_type
            ]
        if not include_inactive:
            certifications = [
                item
                for item in certifications
                if _certification_active(item, now=current_time)
            ]
        return sorted(certifications, key=lambda item: (item.agent_id, item.certified_at))

    def check_autonomy_certification(
        self,
        *,
        agent_id: str,
        requested_autonomy_level: AgentGovernanceAutonomyLevel,
        now: datetime | None = None,
        incidents: list[AgentIncident] | None = None,
        changes: list[CertificationChangeEvent] | None = None,
    ) -> AgentAutonomyCertificationDecision:
        current_time = now or datetime.now(UTC)
        certification = self._best_active_autonomy_certification(
            agent_id=agent_id,
            now=current_time,
        )
        recertification = self.check_recertification_required(
            agent_id=agent_id,
            certification=certification,
            incidents=incidents,
            changes=changes,
            now=current_time,
        )
        if certification is None:
            return AgentAutonomyCertificationDecision(
                allowed=_autonomy_lte(
                    requested_autonomy_level,
                    DEFAULT_UNCERTIFIED_AUTONOMY_CAP,
                ),
                requested_autonomy_level=requested_autonomy_level,
                effective_autonomy_cap=DEFAULT_UNCERTIFIED_AUTONOMY_CAP,
                certification=None,
                reason="Higher autonomy requires an active certification.",
                requires_recertification=recertification.required,
                recertification_reasons=recertification.reasons,
            )
        if recertification.required:
            return AgentAutonomyCertificationDecision(
                allowed=False,
                requested_autonomy_level=requested_autonomy_level,
                effective_autonomy_cap=DEFAULT_UNCERTIFIED_AUTONOMY_CAP,
                certification=certification,
                reason="Active certification requires recertification before higher autonomy.",
                requires_recertification=True,
                recertification_reasons=recertification.reasons,
            )
        allowed = _autonomy_lte(
            requested_autonomy_level,
            certification.certified_autonomy_level,
        )
        return AgentAutonomyCertificationDecision(
            allowed=allowed,
            requested_autonomy_level=requested_autonomy_level,
            effective_autonomy_cap=certification.certified_autonomy_level,
            certification=certification,
            reason=(
                "Requested autonomy is certified."
                if allowed
                else "Requested autonomy exceeds active certification."
            ),
            requires_recertification=False,
            recertification_reasons=[],
        )

    def check_recertification_required(
        self,
        *,
        agent_id: str,
        certification: AgentCertification | None = None,
        incidents: list[AgentIncident] | None = None,
        changes: list[CertificationChangeEvent] | None = None,
        now: datetime | None = None,
    ) -> CertificationRecertificationDecision:
        current_time = now or datetime.now(UTC)
        active_certification = certification or self._best_active_autonomy_certification(
            agent_id=agent_id,
            now=current_time,
        )
        reasons: list[str] = []
        if active_certification is None:
            return CertificationRecertificationDecision(
                required=True,
                reasons=["No active certification exists."],
                certification=None,
            )
        if (
            active_certification.expires_at is not None
            and active_certification.expires_at < current_time
        ):
            reasons.append("Certification is expired.")
        for change in changes or []:
            if change.agent_id != agent_id:
                continue
            if change.changed_at >= active_certification.certified_at:
                reasons.append(f"Recertification required after {change.change_type}.")
        for incident in incidents or []:
            if incident.agent_id != agent_id:
                continue
            if (
                incident.severity in HIGH_INCIDENT_SEVERITIES
                and incident.opened_at >= active_certification.certified_at
            ):
                reasons.append(
                    f"Recertification required after {incident.severity} incident."
                )
        required = bool(reasons)
        if required:
            self._audit(
                certification_id=active_certification.certification_id,
                action="recertification_required",
                actor_id="system",
                summary="Agent certification requires recertification.",
                metadata={"reasons": reasons},
            )
            self._persist()
        return CertificationRecertificationDecision(
            required=required,
            reasons=reasons,
            certification=active_certification,
        )

    def _best_active_autonomy_certification(
        self,
        *,
        agent_id: str,
        now: datetime,
    ) -> AgentCertification | None:
        active = [
            certification
            for certification in self.certifications
            if certification.agent_id == agent_id
            and certification.certification_type == "autonomy_level"
            and _certification_active(certification, now=now)
        ]
        if not active:
            return None
        return max(
            active,
            key=lambda item: (
                AUTONOMY_ORDER[item.certified_autonomy_level],
                item.certified_at,
            ),
        )

    def _require_certification(self, certification_id: str) -> AgentCertification:
        for certification in self.certifications:
            if certification.certification_id == certification_id:
                return certification
        raise AgentCertificationError(f"Unknown agent certification: {certification_id}")

    def _audit(
        self,
        *,
        certification_id: str | None,
        action: CertificationAuditAction,
        actor_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentCertificationAuditEvent:
        event = AgentCertificationAuditEvent(
            audit_event_id=f"agent-certification-audit-{uuid4().hex[:12]}",
            certification_id=certification_id,
            action=action,
            actor_id=actor_id,
            occurred_at=datetime.now(UTC),
            summary=summary,
            metadata=metadata or {},
        )
        self.audit_events.append(event)
        return event

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save_certifications(self.certifications, self.audit_events)


def _certification_block_reason(
    *,
    agent_id: str,
    certification_type: AgentCertificationType,
    authorization: AgentCertificationAuthorization,
    evaluation_results: list[CertificationEvaluationResult],
    guardrail_benchmark_passed: bool,
    role_tests_passed: bool,
    policy_compliant: bool,
    recent_incidents: list[AgentIncident],
) -> str | None:
    if authorization.actor_id == agent_id:
        return "Agents cannot self-certify."
    if _is_codex_actor(authorization):
        return "Codex cannot certify agents."
    if authorization.actor_type not in AUTHORIZED_CERTIFICATION_ACTORS:
        return "Certification requires human, admin, or service-account authorization."
    if not authorization.can_certify(certification_type):
        return "Certification exceeds authorizing actor permission scope."
    if not evaluation_results:
        return "Certification requires a completed evaluation suite."
    failed = [result.suite_name for result in evaluation_results if not result.passed]
    if failed:
        return f"Certification blocked by failed eval suite: {', '.join(sorted(failed))}."
    if not guardrail_benchmark_passed:
        return "Certification blocked by failed guardrail benchmark."
    if not role_tests_passed:
        return "Certification blocked by failed role-specific tests."
    if not policy_compliant:
        return "Certification blocked by policy compliance failure."
    if any(incident.severity in HIGH_INCIDENT_SEVERITIES for incident in recent_incidents):
        return "Certification blocked until high or critical incidents are recertified."
    return None


def _is_codex_actor(authorization: AgentCertificationAuthorization) -> bool:
    return (
        authorization.actor_type == "codex"
        or authorization.actor_id.strip().lower() in CODEX_ACTOR_IDS
    )


def _certification_score(evaluation_results: list[CertificationEvaluationResult]) -> float:
    if not evaluation_results:
        return 0.0
    return min(result.score for result in evaluation_results)


def _certification_active(
    certification: AgentCertification,
    *,
    now: datetime,
) -> bool:
    return (
        certification.passed
        and certification.metadata.get("revoked") is not True
        and (certification.expires_at is None or certification.expires_at >= now)
    )


def _autonomy_lte(
    requested: AgentGovernanceAutonomyLevel,
    allowed: AgentGovernanceAutonomyLevel,
) -> bool:
    return AUTONOMY_ORDER[requested] <= AUTONOMY_ORDER[allowed]


__all__ = [
    "AgentAutonomyCertificationDecision",
    "AgentCertification",
    "AgentCertificationAuditEvent",
    "AgentCertificationAuthorization",
    "AgentCertificationDecision",
    "AgentCertificationError",
    "AgentCertificationManager",
    "AgentCertificationStore",
    "CertificationChangeEvent",
    "CertificationEvaluationResult",
    "CertificationRecertificationDecision",
]
