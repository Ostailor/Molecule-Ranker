from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

INTEGRATION_DRY_RUN_SYNC = RuntimeSkillSpec(
    skill_name="integration_dry_run_sync",
    description="Check integration health and run a read-only dry-run sync for review.",
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "integration_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="health_check_integration",
            tool_name="health_check_integration",
            expected_outputs=["integration_health"],
        ),
        RuntimeSkillStepTemplate(
            action_type="dry_run_sync",
            tool_name="dry_run_sync",
            expected_outputs=["dry_run_sync_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="summarize_artifacts",
            tool_name="summarize_artifacts",
            expected_outputs=["sync_review_summary"],
        ),
    ],
    required_tools=["health_check_integration", "dry_run_sync", "summarize_artifacts"],
    required_permissions=["integration:read", "codex:run"],
    approval_requirements=[],
    expected_artifacts=["integration_health", "dry_run_sync_report", "sync_review_summary"],
    guardrails=[
        "Dry-run sync must not write to external systems.",
        "External write sync requires separate explicit approval.",
        "Credentials and external secrets must not be exposed.",
    ],
)

__all__ = ["INTEGRATION_DRY_RUN_SYNC"]
