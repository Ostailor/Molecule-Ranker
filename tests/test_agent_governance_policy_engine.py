from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.agent_governance.policies import (
    AgentActionRequest,
    AgentGovernancePolicyEngine,
    default_platform_policy,
)
from molecule_ranker.agent_governance.schemas import AgentGovernancePolicy, AgentRunControl

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_org_denylist_wins_over_project_allowlist() -> None:
    engine = AgentGovernancePolicyEngine(
        org_policies=[
            _policy(
                "org-policy",
                org_id="org-1",
                denied_tool_categories=["generation"],
            )
        ],
        project_policies=[
            _policy(
                "project-policy",
                org_id="org-1",
                project_id="project-1",
                allowed_tool_categories=["generation"],
            )
        ],
    )

    decision = engine.evaluate_action(
        _request(
            tool_category="generation",
            org_id="org-1",
            project_id="project-1",
        )
    )

    assert decision.status == "blocked"
    assert decision.violations[0].violation_type == "denied_tool_category"
    assert "org-policy" in decision.effective_policy.policy_ids
    assert "project-policy" in decision.effective_policy.policy_ids


def test_emergency_kill_switch_blocks_everything() -> None:
    engine = AgentGovernancePolicyEngine(
        run_controls=[
            AgentRunControl(
                control_id="kill-1",
                org_id="org-1",
                project_id=None,
                agent_id=None,
                control_type="kill_switch",
                reason="Incident response.",
                applied_by="admin-1",
                applied_at=NOW,
                expires_at=None,
                active=True,
                metadata={},
            )
        ]
    )

    decision = engine.evaluate_action(_request(org_id="org-1"))

    assert decision.status == "blocked"
    assert decision.violations[0].violation_type == "emergency_kill_switch"


def test_project_policy_narrows_allowed_tools() -> None:
    engine = AgentGovernancePolicyEngine(
        org_policies=[
            _policy(
                "org-policy",
                org_id="org-1",
                allowed_tool_categories=["ranking", "review"],
            )
        ],
        project_policies=[
            _policy(
                "project-policy",
                org_id="org-1",
                project_id="project-1",
                allowed_tool_categories=["ranking"],
            )
        ],
    )

    allowed = engine.evaluate_action(
        _request(tool_category="ranking", org_id="org-1", project_id="project-1")
    )
    blocked = engine.evaluate_action(
        _request(tool_category="review", org_id="org-1", project_id="project-1")
    )

    assert allowed.status == "allowed"
    assert allowed.effective_policy.allowed_tool_categories == ["ranking"]
    assert blocked.status == "blocked"
    assert blocked.violations[0].violation_type == "tool_category_not_allowed"


def test_autonomy_level_capped_by_strictest_policy() -> None:
    engine = AgentGovernancePolicyEngine(
        org_policies=[
            _policy("org-policy", org_id="org-1", max_autonomy_level="suggest_only")
        ],
        project_policies=[
            _policy(
                "project-policy",
                org_id="org-1",
                project_id="project-1",
                max_autonomy_level="supervised_auto",
            )
        ],
    )

    decision = engine.evaluate_action(
        _request(
            autonomy_level="execute_safe_tools",
            org_id="org-1",
            project_id="project-1",
        )
    )

    assert decision.status == "blocked"
    assert decision.autonomy_allowed is False
    assert decision.effective_policy.max_autonomy_level == "suggest_only"
    assert decision.violations[0].violation_type == "autonomy_level_capped"


def test_external_write_approval_required() -> None:
    engine = AgentGovernancePolicyEngine()

    decision = engine.evaluate_action(
        _request(action="run_external_sync_write", side_effect_level="external_write")
    )

    assert decision.status == "approval_required"
    assert decision.requires_approval is True
    assert "external_write" in decision.required_approval_actions
    assert decision.violations == []


def test_generated_molecule_advancement_blocked_without_human_approval() -> None:
    engine = AgentGovernancePolicyEngine()

    blocked = engine.evaluate_action(
        _request(action="advance_generated_molecule_to_assay", tool_category="campaign")
    )
    approved = engine.evaluate_action(
        _request(
            action="advance_generated_molecule_to_assay",
            tool_category="campaign",
            human_approved_actions={"generated_molecule_human_review"},
        )
    )

    assert blocked.status == "blocked"
    assert blocked.violations[0].violation_type == (
        "generated_molecule_without_human_approval"
    )
    assert approved.status == "allowed"


def test_codex_worker_restricted_action_blocks() -> None:
    engine = AgentGovernancePolicyEngine()

    decision = engine.evaluate_action(
        _request(
            agent_type="codex_worker",
            action="approve_policy_override",
            tool_category="governance",
        )
    )

    assert decision.status == "blocked"
    assert any(
        violation.violation_type == "blocked_action"
        for violation in decision.violations
    )


def _request(
    *,
    agent_type: str = "runtime_agent",
    action: str = "run_ranking",
    autonomy_level: str = "execute_safe_tools",
    org_id: str | None = None,
    project_id: str | None = None,
    campaign_id: str | None = None,
    tool_category: str = "ranking",
    side_effect_level: str = "artifact_write",
    human_approved_actions: set[str] | None = None,
) -> AgentActionRequest:
    return AgentActionRequest.model_validate(
        {
            "agent_id": "agent-1",
            "agent_type": agent_type,
            "action": action,
            "autonomy_level": autonomy_level,
            "org_id": org_id,
            "project_id": project_id,
            "campaign_id": campaign_id,
            "tool_category": tool_category,
            "side_effect_level": side_effect_level,
            "human_approved_actions": human_approved_actions or set(),
        }
    )


def _policy(
    policy_id: str,
    *,
    org_id: str | None = None,
    project_id: str | None = None,
    campaign_id: str | None = None,
    max_autonomy_level: str = "execute_with_approval",
    allowed_tool_categories: list[str] | None = None,
    denied_tool_categories: list[str] | None = None,
    allowed_side_effect_levels: list[str] | None = None,
    approval_required_actions: list[str] | None = None,
    blocked_actions: list[str] | None = None,
) -> AgentGovernancePolicy:
    base = default_platform_policy().model_dump()
    base.update(
        {
            "policy_id": policy_id,
            "org_id": org_id,
            "project_id": project_id,
            "policy_name": policy_id,
            "max_autonomy_level": max_autonomy_level,
            "allowed_tool_categories": allowed_tool_categories or [],
            "denied_tool_categories": denied_tool_categories or [],
            "allowed_side_effect_levels": allowed_side_effect_levels
            if allowed_side_effect_levels is not None
            else ["none", "artifact_write", "external_read"],
            "approval_required_actions": approval_required_actions or [],
            "blocked_actions": blocked_actions or [],
            "metadata": {"campaign_id": campaign_id} if campaign_id else {},
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    return AgentGovernancePolicy.model_validate(base)
