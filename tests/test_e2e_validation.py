from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import EndToEndWorkflowRunner, WorkflowRunRequest

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def _mocked_full_loop_result():
    return EndToEndWorkflowRunner(now=lambda: NOW).run(
        WorkflowRunRequest(
            workflow_type="full_discovery_loop",
            mode="mocked",
            disease_name="Example disease",
            project_id="project-1",
            requested_by="user-1",
        )
    )


def test_mocked_full_loop_validates() -> None:
    run_result = _mocked_full_loop_result()

    validation = EndToEndWorkflowValidator(now=lambda: NOW).validate_run_result(run_result)

    assert validation.passed is True
    assert validation.required_artifacts_present is True
    assert validation.lineage_complete is True
    assert validation.guardrails_passed is True
    assert validation.external_sync_validated is True
    assert validation.approvals_satisfied is True


def test_missing_artifact_fails() -> None:
    run_result = _mocked_full_loop_result()
    assert run_result.bundle is not None
    broken_bundle = run_result.bundle.model_copy(update={"key_artifact_ids": []})

    validation = EndToEndWorkflowValidator(now=lambda: NOW).validate(
        workflow=run_result.workflow,
        steps=run_result.steps,
        bundle=broken_bundle,
        lineage_records=run_result.lineage_records,
    )

    assert validation.passed is False
    assert validation.required_artifacts_present is False
    assert any("required artifacts" in finding for finding in validation.findings)


def test_missing_lineage_fails() -> None:
    run_result = _mocked_full_loop_result()
    assert run_result.bundle is not None

    validation = EndToEndWorkflowValidator(now=lambda: NOW).validate(
        workflow=run_result.workflow,
        steps=run_result.steps,
        bundle=run_result.bundle,
        lineage_records=[],
    )

    assert validation.passed is False
    assert validation.lineage_complete is False
    assert any("lineage" in finding for finding in validation.findings)


def test_generated_overclaim_fails() -> None:
    run_result = _mocked_full_loop_result()
    assert run_result.bundle is not None
    overclaim = run_result.bundle.model_copy(
        update={
            "generated_summary": {
                "claim": "Generated molecule has activity and proven safety."
            }
        }
    )

    validation = EndToEndWorkflowValidator(now=lambda: NOW).validate(
        workflow=run_result.workflow,
        steps=run_result.steps,
        bundle=overclaim,
        lineage_records=run_result.lineage_records,
    )

    assert validation.passed is False
    assert validation.guardrails_passed is False
    assert any("forbidden text" in finding for finding in validation.findings)


def test_validate_e2e_cli_mocked_full_loop() -> None:
    result = CliRunner().invoke(
        app,
        ["validate", "e2e", "--workflow", "full_discovery_loop", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metadata"]["workflow_type"] == "full_discovery_loop"
