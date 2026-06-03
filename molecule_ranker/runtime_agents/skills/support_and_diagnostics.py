from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

DIAGNOSE_FAILED_JOB = RuntimeSkillSpec(
    skill_name="diagnose_failed_job",
    description="Inspect failed jobs and draft a guarded failure explanation.",
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "job_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="explain_failure",
            tool_name="explain_failure",
            expected_outputs=["failure_explanation"],
        ),
        RuntimeSkillStepTemplate(
            action_type="plan_followup",
            tool_name="plan_followup",
            expected_outputs=["followup_plan"],
        ),
    ],
    required_tools=["explain_failure", "plan_followup"],
    required_permissions=["codex:run"],
    approval_requirements=[],
    expected_artifacts=["failure_explanation", "followup_plan"],
    guardrails=[
        "Failure explanations must use runtime artifacts only.",
        "Codex cannot invent missing job logs.",
        "Follow-up plans must use registered tools only.",
    ],
)

GENERATE_SUPPORT_BUNDLE = RuntimeSkillSpec(
    skill_name="generate_support_bundle",
    description="Run readiness checks and generate a redacted support bundle.",
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "include_logs": {"type": "boolean"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="generate_support_bundle",
            tool_name="generate_support_bundle",
            approval_requirements=["support_bundle_logs"],
            expected_outputs=["support_bundle"],
        ),
        RuntimeSkillStepTemplate(
            action_type="run_readiness",
            tool_name="run_readiness",
            expected_outputs=["readiness_report"],
        ),
    ],
    required_tools=["generate_support_bundle", "run_readiness"],
    required_permissions=["support:bundle", "admin:readiness"],
    approval_requirements=[],
    expected_artifacts=["support_bundle", "readiness_report"],
    guardrails=[
        "Support bundles must be redacted.",
        "Logs and transcripts require retention-policy approval.",
        "Secrets must not be stored or exported.",
    ],
)

__all__ = ["DIAGNOSE_FAILED_JOB", "GENERATE_SUPPORT_BUNDLE"]
