from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

RUN_EVALUATION_SUITE = RuntimeSkillSpec(
    skill_name="run_evaluation_suite",
    description=(
        "Run benchmark, guardrail, and reproducibility checks and create validation handoff."
    ),
    input_schema=_object_schema(
        {
            "project_id": {"type": "string"},
            "suite_id": {"type": "string"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="run_benchmark",
            tool_name="run_benchmark",
            expected_outputs=["benchmark_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="run_guardrail_benchmark",
            tool_name="run_guardrail_benchmark",
            expected_outputs=["guardrail_benchmark_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="run_reproducibility_check",
            tool_name="run_reproducibility_check",
            expected_outputs=["reproducibility_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="create_validation_handoff",
            tool_name="create_validation_handoff",
            expected_outputs=["validation_handoff"],
        ),
    ],
    required_tools=[
        "run_benchmark",
        "run_guardrail_benchmark",
        "run_reproducibility_check",
        "create_validation_handoff",
    ],
    required_permissions=["evaluation:run", "review:write"],
    approval_requirements=[],
    expected_artifacts=[
        "benchmark_report",
        "guardrail_benchmark_report",
        "reproducibility_report",
        "validation_handoff",
    ],
    guardrails=[
        "Evaluation reports must preserve benchmark provenance.",
        "Prospective predictions must not be edited after freezing.",
        "Validation handoff is reviewable, not automatic approval.",
    ],
)

__all__ = ["RUN_EVALUATION_SUITE"]
