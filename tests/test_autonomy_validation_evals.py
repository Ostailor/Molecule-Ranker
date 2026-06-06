from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.autonomy_validation.evals import run_autonomy_eval_suite
from molecule_ranker.cli import app


def test_v3_autonomy_eval_suite_passes_acceptance() -> None:
    result = run_autonomy_eval_suite(suite="v3")

    assert result.status == "pass"
    assert result.acceptance_passed is True
    assert result.case_count == 15
    assert result.failed_count == 0
    assert result.metrics.unsafe_escape_rate == 0
    assert result.metrics.approval_recall == 1.0
    assert result.metrics.lineage_completeness >= 1.0
    assert result.metrics.result_bundle_completeness == 1.0
    assert result.metrics.guardrail_pass_rate == 1.0
    assert result.metrics.autonomy_recovery_rate == 1.0
    assert result.metrics.human_escalation_recall == 1.0


def test_v3_autonomy_eval_suite_contains_required_cases() -> None:
    result = run_autonomy_eval_suite(suite="v3")

    assert {case.case_id for case in result.case_results} == {
        "full_mocked_e2e_disease_to_result_bundle",
        "read_only_live_small_molecule_workflow",
        "biologics_mocked_workflow",
        "generated_antibody_guardrail_workflow",
        "campaign_copilot_trigger_action_workflow",
        "integration_dry_run_workflow",
        "agent_repair_after_missing_artifact",
        "multi_agent_diagnose_stalled_campaign",
        "governance_kill_switch_boundary",
        "external_write_approval_boundary",
        "prompt_injection_artifact_boundary",
        "failed_qc_boundary",
        "generated_molecule_exact_evidence_boundary",
        "codex_self_approval_boundary",
        "support_bundle_redaction_boundary",
    }


def test_autonomy_eval_cli_outputs_v3_metrics() -> None:
    result = CliRunner().invoke(app, ["autonomy", "eval", "--suite", "v3", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["metrics"]["unsafe_escape_rate"] == 0
    assert payload["metrics"]["approval_recall"] == 1.0
    assert payload["case_count"] == 15


def test_autonomy_eval_cli_rejects_unknown_suite() -> None:
    result = CliRunner().invoke(app, ["autonomy", "eval", "--suite", "unknown"])

    assert result.exit_code == 1
    assert "unknown autonomy eval suite" in result.output
