from __future__ import annotations

from molecule_ranker.subagents.registry import (
    EVIDENCE_CREATING_TOOL_CATEGORIES,
    HIGH_RISK_TOOL_CATEGORIES,
    BiologicsEngineerSubagent,
    MoleculeDesignerSubagent,
    SubagentRegistry,
    builtin_subagent_profiles,
)

EXPECTED_PROFILE_IDS = {
    "program-manager",
    "evidence-reviewer",
    "molecule-designer",
    "biologics-engineer",
    "developability-safety",
    "experiment-analyst",
    "predictive-modeler",
    "structure-reviewer",
    "graph-reasoner",
    "hypothesis-planner",
    "portfolio-strategist",
    "campaign-planner",
    "integration-operator",
    "evaluation-validator",
    "guardrail-sentinel",
    "platform-operator",
}


def test_builtin_subagent_profiles_cover_all_roles() -> None:
    profiles = builtin_subagent_profiles()
    registry = SubagentRegistry()

    assert {profile.subagent_id for profile in profiles} == EXPECTED_PROFILE_IDS
    assert registry.by_role("evidence_reviewer").subagent_id == "evidence-reviewer"
    assert registry.by_role("biologics_engineer").subagent_id == "biologics-engineer"
    assert registry.require("program-manager").can_delegate is True


def test_every_profile_has_allowed_and_denied_tool_categories() -> None:
    for profile in builtin_subagent_profiles():
        assert profile.allowed_tool_categories, profile.subagent_id
        assert profile.denied_tool_categories, profile.subagent_id
        assert not set(profile.allowed_tool_categories).intersection(
            profile.denied_tool_categories
        )


def test_high_risk_tool_categories_require_approval_for_every_profile() -> None:
    high_risk = set(HIGH_RISK_TOOL_CATEGORIES)

    for profile in builtin_subagent_profiles():
        assert high_risk.issubset(set(profile.metadata["high_risk_tool_categories"]))
        assert high_risk.issubset(set(profile.metadata["approval_required_tool_categories"]))


def test_evidence_creating_actions_are_denied_where_appropriate() -> None:
    evidence_creating = set(EVIDENCE_CREATING_TOOL_CATEGORIES)

    for profile in builtin_subagent_profiles():
        assert evidence_creating.issubset(set(profile.denied_tool_categories))
        assert profile.metadata["evidence_creating_actions_denied"] is True

    evidence_reviewer = SubagentRegistry().require("evidence-reviewer")
    assert "invent evidence" in evidence_reviewer.metadata["cannot"]
    assert "invent citations" in evidence_reviewer.metadata["cannot"]


def test_generated_molecule_boundaries_are_present() -> None:
    boundaries = MoleculeDesignerSubagent.metadata["generated_molecule_boundaries"]

    assert "claim activity" in MoleculeDesignerSubagent.metadata["cannot"]
    assert "create molecules outside generation pipeline" in MoleculeDesignerSubagent.metadata[
        "cannot"
    ]
    assert any("computational hypotheses" in boundary for boundary in boundaries)
    assert any("approved generation/design tools" in boundary for boundary in boundaries)


def test_biologics_engineer_profile_exists_with_guardrails_and_tasks() -> None:
    profile = BiologicsEngineerSubagent

    assert profile.subagent_id == "biologics-engineer"
    assert profile.role == "biologics_engineer"
    assert "antibody_validation" in profile.allowed_tool_categories
    assert "antibody_novelty" in profile.allowed_tool_categories
    assert "sequence_fabrication" in profile.denied_tool_categories
    assert "assay_result_creation" in profile.denied_tool_categories
    assert "invent sequences" in profile.metadata["cannot"]
    assert "invent epitopes" in profile.metadata["cannot"]
    assert "invent binding claims" in profile.metadata["cannot"]
    assert "provide expression/purification/lab protocols" in profile.metadata["cannot"]
    assert "approve generated antibody advancement" in profile.metadata["cannot"]
    assert profile.metadata["codex_tasks"] == [
        "summarize_biologic_candidate",
        "explain_antibody_liabilities",
        "draft_biologics_review_questions",
        "summarize_antigen_context",
        "draft_biologics_campaign_summary",
    ]


def test_specific_cannot_rules_for_gate_protocol_prediction_and_platform_boundaries() -> None:
    registry = SubagentRegistry()

    assert "approve gates" in registry.require("program-manager").metadata["cannot"]
    assert "approve stage gates" in registry.require("portfolio-strategist").metadata["cannot"]
    assert "create lab protocols" in registry.require("campaign-planner").metadata["cannot"]
    assert "turn predictions into evidence" in registry.require("predictive-modeler").metadata[
        "cannot"
    ]
    assert "external write without approval" in registry.require("integration-operator").metadata[
        "cannot"
    ]
    assert "access secrets or bypass RBAC" in registry.require("platform-operator").metadata[
        "cannot"
    ]
