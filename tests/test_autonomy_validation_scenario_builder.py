from __future__ import annotations

import pytest

from molecule_ranker.autonomy_validation.scenario_builder import (
    build_builtin_autonomy_scenarios,
    get_builtin_autonomy_scenario,
    list_builtin_autonomy_scenario_ids,
)

EXPECTED_SCENARIOS = [
    "small_molecule_readonly_e2e",
    "small_molecule_generation_mocked_e2e",
    "biologics_mocked_e2e",
    "biologics_generation_guarded_mocked",
    "integration_dry_run_e2e",
    "campaign_copilot_monitoring",
    "multi_agent_diagnose_campaign",
    "repair_recovery_missing_artifact",
    "governance_boundary_external_write",
    "v3_full_demo_mocked",
]


def test_builtin_scenario_catalog_contains_v3_autonomy_scenarios() -> None:
    scenarios = build_builtin_autonomy_scenarios()

    assert [scenario.scenario_id for scenario in scenarios] == EXPECTED_SCENARIOS
    assert list_builtin_autonomy_scenario_ids() == EXPECTED_SCENARIOS
    assert len({scenario.scenario_id for scenario in scenarios}) == len(EXPECTED_SCENARIOS)


def test_builtin_scenarios_do_not_require_live_external_writes() -> None:
    scenarios = build_builtin_autonomy_scenarios()

    for scenario in scenarios:
        assert scenario.metadata["requires_live_external_write"] is False
        assert scenario.metadata["external_writes_allowed"] is False
        if scenario.mode != "write_approved_live":
            assert scenario.metadata["external_writes_allowed"] is False
        if scenario.mode in {"read_only_live", "dry_run"}:
            assert "external_writes_performed_equals_zero" in scenario.success_criteria or (
                scenario.scenario_id == "governance_boundary_external_write"
                and "external_write_not_performed" in scenario.success_criteria
            )


def test_builtin_scenarios_specify_artifacts_forbidden_outputs_and_guardrails() -> None:
    for scenario in build_builtin_autonomy_scenarios():
        assert scenario.expected_artifacts, scenario.scenario_id
        assert scenario.forbidden_outputs, scenario.scenario_id
        assert scenario.required_guardrails, scenario.scenario_id
        assert "medical_advice" in scenario.forbidden_outputs
        assert "fabricated_evidence" in scenario.forbidden_outputs
        assert "no_fabricated_evidence" in scenario.required_guardrails
        assert scenario.success_criteria, scenario.scenario_id


def test_specific_scenario_modes_and_generation_controls() -> None:
    readonly = get_builtin_autonomy_scenario("small_molecule_readonly_e2e")
    generation = get_builtin_autonomy_scenario("small_molecule_generation_mocked_e2e")
    biologics = get_builtin_autonomy_scenario("biologics_mocked_e2e")
    guarded = get_builtin_autonomy_scenario("biologics_generation_guarded_mocked")
    integration = get_builtin_autonomy_scenario("integration_dry_run_e2e")

    assert readonly.mode == "read_only_live"
    assert readonly.scenario_type == "small_molecule_e2e"
    assert readonly.metadata["generation_enabled"] is False
    assert generation.mode == "mocked"
    assert generation.scenario_type == "generated_molecule_e2e"
    assert generation.metadata["generation_enabled"] is True
    assert biologics.metadata["antibody_generation_enabled"] is False
    assert guarded.metadata["antibody_generation_enabled"] is True
    assert guarded.metadata["generator_kind"] == "null_conservative"
    assert integration.mode == "dry_run"


def test_builtin_scenario_lookup_returns_defensive_copy() -> None:
    scenario = get_builtin_autonomy_scenario("v3_full_demo_mocked")
    scenario.expected_artifacts.append("mutated")

    fresh = get_builtin_autonomy_scenario("v3_full_demo_mocked")

    assert "mutated" not in fresh.expected_artifacts


def test_unknown_builtin_scenario_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown autonomy scenario"):
        get_builtin_autonomy_scenario("unknown")
