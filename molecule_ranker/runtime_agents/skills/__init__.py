from __future__ import annotations

from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    RuntimeSkillValidationError,
    expand_skill_to_plan,
)
from molecule_ranker.runtime_agents.skills.evaluate_and_report import RUN_EVALUATION_SUITE
from molecule_ranker.runtime_agents.skills.experiment_feedback import IMPORT_RESULTS_AND_REPLAN
from molecule_ranker.runtime_agents.skills.generate_and_triage import SKILL as GENERATE_AND_TRIAGE
from molecule_ranker.runtime_agents.skills.graph_hypothesis_campaign import (
    BUILD_GRAPH_AND_HYPOTHESES,
    OPTIMIZE_PORTFOLIO_AND_CAMPAIGN,
)
from molecule_ranker.runtime_agents.skills.integration_sync_review import INTEGRATION_DRY_RUN_SYNC
from molecule_ranker.runtime_agents.skills.rank_and_review import SKILL as RANK_AND_REVIEW
from molecule_ranker.runtime_agents.skills.support_and_diagnostics import (
    DIAGNOSE_FAILED_JOB,
    GENERATE_SUPPORT_BUNDLE,
)


def default_runtime_skills() -> dict[str, RuntimeSkillSpec]:
    skills = [
        RANK_AND_REVIEW,
        GENERATE_AND_TRIAGE,
        IMPORT_RESULTS_AND_REPLAN,
        BUILD_GRAPH_AND_HYPOTHESES,
        OPTIMIZE_PORTFOLIO_AND_CAMPAIGN,
        RUN_EVALUATION_SUITE,
        DIAGNOSE_FAILED_JOB,
        GENERATE_SUPPORT_BUNDLE,
        INTEGRATION_DRY_RUN_SYNC,
    ]
    return {skill.skill_name: skill for skill in skills}


def get_runtime_skill(skill_name: str) -> RuntimeSkillSpec:
    try:
        return default_runtime_skills()[skill_name]
    except KeyError as exc:
        raise RuntimeSkillValidationError(f"Unknown runtime skill: {skill_name}") from exc


__all__ = [
    "RuntimeSkillSpec",
    "RuntimeSkillStepTemplate",
    "RuntimeSkillValidationError",
    "default_runtime_skills",
    "expand_skill_to_plan",
    "get_runtime_skill",
]
