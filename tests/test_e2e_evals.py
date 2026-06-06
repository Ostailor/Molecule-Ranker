from __future__ import annotations

import json
from collections.abc import Sequence

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.e2e.evals import (
    E2E_EVAL_CASES,
    EndToEndEvalCaseResult,
    EndToEndEvalSuite,
    run_end_to_end_eval_suite,
)


def test_e2e_eval_suite_runs() -> None:
    result = run_end_to_end_eval_suite(suite="default")

    assert result.suite == "default"
    assert result.case_count == len(E2E_EVAL_CASES) == 10
    assert result.acceptance_passed is True
    assert result.status == "pass"
    assert result.metrics.external_write_escape_rate == 0
    assert result.metrics.generated_overclaim_rate == 0
    mocked = _case(result.case_results, "mocked_full_discovery_loop")
    assert mocked.status == "passed"
    assert mocked.workflow_success is True


def test_e2e_eval_red_team_cases_fail_safely() -> None:
    result = run_end_to_end_eval_suite(suite="default")

    for case_id in {
        "missing_artifact_detection",
        "external_mapping_conflict",
        "failed_qc_import",
        "generated_exact_result_rule",
        "codex_summary_guardrails",
    }:
        case = _case(result.case_results, case_id)
        assert case.status == "failed_safely"
        assert case.guardrail_passed is True
        assert case.external_write_escape is False
    generated_case = _case(result.case_results, "generated_exact_result_rule")
    assert generated_case.generated_overclaim_escape is False


def test_e2e_eval_cli_runs_default_suite() -> None:
    result = CliRunner().invoke(app, ["e2e", "eval", "--suite", "default", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["suite"] == "default"
    assert payload["status"] == "pass"
    assert payload["case_count"] == len(E2E_EVAL_CASES)
    assert payload["metrics"]["external_write_escape_rate"] == 0
    assert payload["metrics"]["generated_overclaim_rate"] == 0


def test_unknown_e2e_eval_case_rejected() -> None:
    suite = EndToEndEvalSuite()

    try:
        suite.run_case("unknown")
    except KeyError as exc:
        assert "unknown e2e eval case" in str(exc)
    else:
        raise AssertionError("unknown eval case should be rejected")


def _case(
    cases: Sequence[EndToEndEvalCaseResult],
    case_id: str,
) -> EndToEndEvalCaseResult:
    return next(case for case in cases if case.case_id == case_id)
