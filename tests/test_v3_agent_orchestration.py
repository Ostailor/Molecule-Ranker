from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.v3.discover import V3DiscoverRequest, run_v3_discover
from molecule_ranker.v3.orchestration import (
    build_v3_default_orchestration,
    validate_v3_orchestration,
)


def test_default_full_discovery_orchestration_includes_required_subagents() -> None:
    orchestration = build_v3_default_orchestration(workflow_type="full_discovery_loop")
    subagents = [subagent.subagent_name for subagent in orchestration.subagents]

    assert orchestration.workflow_type == "full_discovery_loop"
    assert orchestration.coordinator_subagent == "ProgramManagerSubagent"
    assert orchestration.guardrail_final_review_subagent == "GuardrailSentinelSubagent"
    assert orchestration.generated_outputs_require_review_gates is True
    assert orchestration.campaign_activation_allowed is False
    assert orchestration.codex_approved_tools_only is True
    for required_subagent in [
        "ProgramManagerSubagent",
        "EvidenceReviewerSubagent",
        "DevelopabilitySafetySubagent",
        "GraphReasonerSubagent",
        "HypothesisPlannerSubagent",
        "PortfolioStrategistSubagent",
        "CampaignPlannerSubagent",
        "EvaluationValidatorSubagent",
        "GuardrailSentinelSubagent",
        "PlatformOperatorSubagent",
    ]:
        assert required_subagent in subagents
    assert "MoleculeDesignerSubagent" not in subagents
    assert "BiologicsEngineerSubagent" not in subagents
    assert "IntegrationOperatorSubagent" not in subagents


def test_optional_subagents_only_participate_when_features_enabled() -> None:
    disabled = build_v3_default_orchestration(
        workflow_type="full_discovery_loop",
        generation_enabled=False,
        biologics_enabled=False,
        integrations_enabled=False,
    )
    enabled = build_v3_default_orchestration(
        workflow_type="full_discovery_loop",
        generation_enabled=True,
        biologics_enabled=True,
        integrations_enabled=True,
    )

    disabled_names = {subagent.subagent_name for subagent in disabled.subagents}
    enabled_names = {subagent.subagent_name for subagent in enabled.subagents}

    assert "MoleculeDesignerSubagent" not in disabled_names
    assert "BiologicsEngineerSubagent" not in disabled_names
    assert "IntegrationOperatorSubagent" not in disabled_names
    assert "MoleculeDesignerSubagent" in enabled_names
    assert "BiologicsEngineerSubagent" in enabled_names
    assert "IntegrationOperatorSubagent" in enabled_names
    assert enabled.subagent("MoleculeDesignerSubagent").review_gate_required is True
    assert enabled.subagent("BiologicsEngineerSubagent").review_gate_required is True
    assert enabled.subagent("IntegrationOperatorSubagent").external_write_allowed is False


def test_default_orchestration_validation_enforces_guardrail_and_approval_rules() -> None:
    orchestration = build_v3_default_orchestration(
        workflow_type="full_discovery_loop",
        generation_enabled=True,
        biologics_enabled=True,
        integrations_enabled=True,
    )

    validation = validate_v3_orchestration(orchestration)

    assert validation.valid is True
    assert validation.issues == []


def test_discover_trace_records_default_orchestration(tmp_path: Path) -> None:
    result = run_v3_discover(
        V3DiscoverRequest(
            disease="Parkinson disease",
            mode="mocked",
            enable_generation=True,
            enable_integrations=True,
            output_dir=tmp_path,
        )
    )

    trace = json.loads(Path(result.artifacts["trace.json"]).read_text(encoding="utf-8"))
    subagents = [
        subagent["subagent_name"]
        for subagent in trace["agent_orchestration"]["subagents"]
    ]

    assert trace["agent_orchestration"]["coordinator_subagent"] == "ProgramManagerSubagent"
    assert (
        trace["agent_orchestration"]["guardrail_final_review_subagent"]
        == "GuardrailSentinelSubagent"
    )
    assert trace["agent_orchestration"]["codex_approved_tools_only"] is True
    assert "MoleculeDesignerSubagent" in subagents
    assert "IntegrationOperatorSubagent" in subagents
    assert "BiologicsEngineerSubagent" not in subagents
