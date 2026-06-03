from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount

PolicyDomain = Literal[
    "scientific_guardrails",
    "codex_usage",
    "external_integrations",
    "generated_molecule_handling",
    "model_prediction_usage",
    "docking_usage",
    "export_package_rules",
    "data_retention",
    "review_stage_gate_requirements",
    "campaign_approval_requirements",
]
PolicyEffect = Literal["allow", "block", "require_review", "require_approval"]
PolicyStatus = Literal["allowed", "blocked", "requires_review", "requires_approval"]
OverrideScope = Literal["project", "org"]

_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "service_token",
    "token",
}


class PolicyRule(BaseModel):
    rule_id: str
    domain: PolicyDomain
    action: str
    effect: PolicyEffect
    message: str
    condition: dict[str, Any] = Field(default_factory=dict)
    can_override: bool = False
    override_permission: str | None = None

    @field_validator("rule_id", "action", "message")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy rule fields must be non-empty")
        return value


class PolicyOverride(BaseModel):
    action: str
    allow: bool = False
    reason: str
    scope: OverrideScope
    approved_by: str

    @field_validator("action", "reason", "approved_by")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy override fields must be non-empty")
        return value


class PolicyEvaluationResult(BaseModel):
    action: str
    allowed: bool
    status: PolicyStatus
    matched_rules: list[PolicyRule] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    override_applied: OverrideScope | None = None
    audit_event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyEngine:
    def __init__(
        self,
        rules: Sequence[PolicyRule] | None = None,
        *,
        project_overrides: Mapping[str, PolicyOverride] | None = None,
        org_overrides: Mapping[str, PolicyOverride] | None = None,
        database: Any | None = None,
    ) -> None:
        self.rules = list(rules or default_policy_pack())
        self.project_overrides = dict(project_overrides or {})
        self.org_overrides = dict(org_overrides or {})
        self.database = database
        self.validate_rules(self.rules)

    @classmethod
    def default(cls, *, database: Any | None = None) -> PolicyEngine:
        return cls(default_policy_pack(), database=database)

    def evaluate(
        self,
        action: str,
        context: Mapping[str, Any] | None = None,
        *,
        user: UserAccount | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        audit: bool = False,
    ) -> PolicyEvaluationResult:
        context_payload = dict(context or {})
        matched_rules = [
            rule
            for rule in self.rules
            if rule.action == action and _condition_matches(rule.condition, context_payload)
        ]
        override = self._matching_override(action, matched_rules)
        if override is not None:
            result = PolicyEvaluationResult(
                action=action,
                allowed=True,
                status="allowed",
                matched_rules=matched_rules,
                requirements=[override.reason],
                override_applied=override.scope,
                metadata={"context": _redact_json(context_payload)},
            )
        else:
            result = self._result_for_rules(action, matched_rules, context_payload)
        if audit and self.database is not None:
            event = self.database.write_audit(
                "policy_evaluated",
                actor_user_id=user.user_id if user is not None else None,
                org_id=org_id,
                project_id=project_id,
                summary=f"Evaluated policy for {action}: {result.status}.",
                object_type="policy",
                object_id=action,
                metadata={
                    "action": action,
                    "status": result.status,
                    "allowed": result.allowed,
                    "matched_rule_ids": [rule.rule_id for rule in result.matched_rules],
                    "violations": result.violations,
                    "requirements": result.requirements,
                    "override_applied": result.override_applied,
                    "context": _redact_json(context_payload),
                },
            )
            result.audit_event_id = event.event_id
        return result

    def explain(self, action: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self.evaluate(action, context)
        rules = [rule for rule in self.rules if rule.action == action]
        return {
            "action": action,
            "result": result.model_dump(mode="json"),
            "rules": [rule.model_dump(mode="json") for rule in rules],
        }

    @staticmethod
    def validate_rules(rules: Sequence[PolicyRule]) -> None:
        seen: set[str] = set()
        domains = {rule.domain for rule in rules}
        for rule in rules:
            if rule.rule_id in seen:
                raise ValueError(f"Duplicate policy rule_id: {rule.rule_id}")
            seen.add(rule.rule_id)
            if rule.can_override and not rule.override_permission:
                raise ValueError(f"Overrideable policy rule lacks permission: {rule.rule_id}")
        missing_domains = set(_REQUIRED_DOMAINS) - domains
        if missing_domains:
            raise ValueError(f"Default policy pack missing domains: {sorted(missing_domains)}")

    def _matching_override(
        self,
        action: str,
        matched_rules: Sequence[PolicyRule],
    ) -> PolicyOverride | None:
        if not matched_rules or not all(rule.can_override for rule in matched_rules):
            return None
        override = self.project_overrides.get(action) or self.org_overrides.get(action)
        if override is not None and override.allow:
            return override
        return None

    def _result_for_rules(
        self,
        action: str,
        matched_rules: Sequence[PolicyRule],
        context: Mapping[str, Any],
    ) -> PolicyEvaluationResult:
        if not matched_rules:
            return PolicyEvaluationResult(
                action=action,
                allowed=True,
                status="allowed",
                metadata={"context": _redact_json(context)},
            )
        if any(rule.effect == "block" for rule in matched_rules):
            return PolicyEvaluationResult(
                action=action,
                allowed=False,
                status="blocked",
                matched_rules=list(matched_rules),
                violations=[rule.message for rule in matched_rules],
                metadata={"context": _redact_json(context)},
            )
        status: PolicyStatus = "requires_approval"
        if any(rule.effect == "require_review" for rule in matched_rules):
            status = "requires_review"
        return PolicyEvaluationResult(
            action=action,
            allowed=False,
            status=status,
            matched_rules=list(matched_rules),
            requirements=[rule.message for rule in matched_rules],
            metadata={"context": _redact_json(context)},
        )


def default_policy_pack() -> list[PolicyRule]:
    return [
        PolicyRule(
            rule_id="generated_molecules_require_review_before_export",
            domain="generated_molecule_handling",
            action="generated_molecule.export",
            effect="block",
            message="Generated molecules require human review before export.",
            condition={"generated_molecule": True, "review_approved": False},
        ),
        PolicyRule(
            rule_id="docking_disabled_unless_project_policy_allows",
            domain="docking_usage",
            action="docking.run",
            effect="block",
            message="Docking is disabled unless the project policy explicitly allows it.",
            condition={"project_policy_allows_docking": False},
            can_override=True,
            override_permission="project:update",
        ),
        PolicyRule(
            rule_id="external_writes_require_admin_approval",
            domain="external_integrations",
            action="integration.external_write",
            effect="require_approval",
            message="External integration writes require admin approval.",
            condition={"admin_approved_external_write": False},
        ),
        PolicyRule(
            rule_id="codex_tasks_cannot_use_raw_assay_files",
            domain="codex_usage",
            action="codex.run_task",
            effect="block",
            message="Codex tasks cannot receive raw assay files.",
            condition={"uses_raw_assay_files": True},
        ),
        PolicyRule(
            rule_id="uncalibrated_predictions_cannot_drive_portfolio_selection",
            domain="model_prediction_usage",
            action="portfolio.select",
            effect="block",
            message="Uncalibrated model predictions cannot influence portfolio selection.",
            condition={"uses_uncalibrated_model_predictions": True},
        ),
        PolicyRule(
            rule_id="generated_molecules_without_evidence_require_triage_review",
            domain="scientific_guardrails",
            action="assay_triage.add_generated",
            effect="block",
            message=(
                "Generated molecules with no direct evidence cannot enter an assay-triage "
                "batch without review."
            ),
            condition={
                "generated_molecule": True,
                "direct_evidence": False,
                "review_approved": False,
            },
        ),
        PolicyRule(
            rule_id="support_bundles_exclude_codex_transcripts_by_default",
            domain="export_package_rules",
            action="support_bundle.generate",
            effect="block",
            message="Support bundles exclude Codex transcripts by default.",
            condition={"include_codex_transcripts": True},
            can_override=True,
            override_permission="admin:manage_org",
        ),
        PolicyRule(
            rule_id="retention_delete_requires_policy_enablement",
            domain="data_retention",
            action="data.delete",
            effect="require_approval",
            message="Data deletion requires retention policy enablement and legal-hold checks.",
            condition={"retention_delete_enabled": False},
        ),
        PolicyRule(
            rule_id="stage_gate_requires_review_decision",
            domain="review_stage_gate_requirements",
            action="stage_gate.advance",
            effect="require_review",
            message="Stage-gate advancement requires a recorded review decision.",
            condition={"review_decision_recorded": False},
        ),
        PolicyRule(
            rule_id="campaign_export_requires_approval",
            domain="campaign_approval_requirements",
            action="campaign.export",
            effect="require_approval",
            message="Campaign export requires campaign approval and must not include protocols.",
            condition={"campaign_approved": False},
        ),
    ]


def project_policy_overrides(
    overrides: Mapping[str, Mapping[str, Any]],
    *,
    actor: UserAccount,
    database: Any,
    project_id: str,
) -> dict[str, PolicyOverride]:
    if not has_permission(actor, "project:update", project_id=project_id, database=database):
        raise PermissionError("Project policy overrides require project:update permission.")
    return _build_overrides(overrides, scope="project", approved_by=actor.user_id)


def org_policy_overrides(
    overrides: Mapping[str, Mapping[str, Any]],
    *,
    actor: UserAccount,
    database: Any,
    org_id: str,
) -> dict[str, PolicyOverride]:
    if not has_permission(actor, "admin:manage_org", org_id=org_id, database=database):
        raise PermissionError("Organization policy overrides require admin:manage_org permission.")
    return _build_overrides(overrides, scope="org", approved_by=actor.user_id)


def _build_overrides(
    overrides: Mapping[str, Mapping[str, Any]],
    *,
    scope: OverrideScope,
    approved_by: str,
) -> dict[str, PolicyOverride]:
    built: dict[str, PolicyOverride] = {}
    for action, payload in overrides.items():
        built[action] = PolicyOverride(
            action=action,
            allow=bool(payload.get("allow", False)),
            reason=str(payload.get("reason") or "Policy override approved."),
            scope=scope,
            approved_by=approved_by,
        )
    return built


def _condition_matches(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return all(context.get(key) == expected for key, expected in condition.items())


def _redact_json(value: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = _redact_secret_keys(dict(value))
    return json.loads(redact_secrets(json.dumps(cleaned, sort_keys=True, default=str)))


def _redact_secret_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            if str(key).lower() in _SECRET_KEYS:
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_secret_keys(nested)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_keys(item) for item in value]
    return value


_REQUIRED_DOMAINS: tuple[PolicyDomain, ...] = (
    "scientific_guardrails",
    "codex_usage",
    "external_integrations",
    "generated_molecule_handling",
    "model_prediction_usage",
    "docking_usage",
    "export_package_rules",
    "data_retention",
    "review_stage_gate_requirements",
    "campaign_approval_requirements",
)


__all__ = [
    "PolicyEngine",
    "PolicyEvaluationResult",
    "PolicyOverride",
    "PolicyRule",
    "default_policy_pack",
    "org_policy_overrides",
    "project_policy_overrides",
]
