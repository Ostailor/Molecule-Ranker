from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.e2e.schemas import EndToEndResultBundle
from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import WorkflowRunResult
from molecule_ranker.v3.certification import V3ResultCertification
from molecule_ranker.v3.discover import V3DiscoverRequest, run_v3_discover

runner = CliRunner()


def test_mocked_discover_succeeds(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "mocked",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    assert payload["workflow_type"] == "full_discovery_loop"
    assert payload["external_writes_performed"] == 0
    assert payload["artifacts"]["candidates.json"] == str(tmp_path / "candidates.json")
    assert (tmp_path / "candidates.json").exists()
    assert (tmp_path / "e2e_result_bundle.md").exists()
    assert (tmp_path / "v3_result_certification.json").exists()
    assert (tmp_path / "trace.json").exists()


def test_mocked_discover_cli_output_shows_runtime_progress(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "mocked",
            "--enable-generation",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    output = result.output
    for expected in [
        "Progress",
        "1. Project created.",
        "2. Disease resolved.",
        "3. Targets retrieved.",
        "4. Candidates ranked.",
        "5. Literature summarized.",
        "6. Generated hypotheses created.",
        "7. Review workspace created.",
        "8. Portfolio/campaign drafted.",
        "9. Result bundle certified.",
        "10. Human review required for generated hypotheses.",
        "Step timeline",
        "Approvals needed",
        "Current agent/subagent activity",
        "Artifacts produced",
        "Warnings and partial success",
        "Recovery suggestions",
        "What you have",
        "What this does not prove",
        "Recommended human review points",
        "not clinical validation",
    ]:
        assert expected in output
    assert "clinically validated" not in output.lower()


def test_dry_run_discover_succeeds_without_external_writes(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "dry_run",
            "--enable-integrations",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    assert payload["external_writes_performed"] == 0
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["safe_defaults"]["external_writes_enabled"] is False
    assert trace["safe_defaults"]["campaign_activation_enabled"] is False
    assert trace["safe_defaults"]["codex_stage_gate_approval_enabled"] is False


def test_read_only_live_discover_refuses_external_writes(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "read_only_live",
            "--enable-integrations",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["external_writes_performed"] == 0
    assert "read_only_live cannot perform external writes" in " ".join(payload["warnings"])
    assert (tmp_path / "trace.json").exists()
    assert not (tmp_path / "e2e_result_bundle.md").exists()


def test_generated_artifacts_are_labeled_computational_hypotheses(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "mocked",
            "--enable-generation",
            "--enable-biologics",
            "--enable-antibody-generation",
            "--require-approval",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    generated = json.loads((tmp_path / "generated_candidates.json").read_text())
    antibodies = json.loads((tmp_path / "generated_antibodies.json").read_text())
    assert generated["label"] == "computational_hypotheses_only"
    assert generated["advanced_without_review"] is False
    assert antibodies["label"] == "computational_hypotheses_only"
    assert antibodies["claims"] == []
    assert antibodies["review_required"] is True


def test_discover_result_bundle_validates(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "discover",
            "--disease",
            "Parkinson disease",
            "--mode",
            "mocked",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    bundle = EndToEndResultBundle.model_validate(
        json.loads((tmp_path / "e2e_result_bundle.json").read_text())
    )
    run_result = WorkflowRunResult.model_validate(
        json.loads((tmp_path / "workflow_result.json").read_text())
    )
    validation = EndToEndWorkflowValidator().validate_run_result(run_result)
    stored_validation = json.loads((tmp_path / "e2e_validation.json").read_text())

    assert bundle.metadata["v3_product_contract"]["product_version"] == "3.0.0"
    assert validation.passed is True
    assert stored_validation["passed"] is True


def test_discover_success_status_blocked_by_failed_certification(
    tmp_path: Path, monkeypatch
) -> None:
    def fail_certification(*args, **kwargs):
        return V3ResultCertification(
            certification_id="failed-cert",
            bundle_id="bundle-1",
            workflow_id="workflow-1",
            project_id="project-1",
            product_version="3.0.0",
            product_contract_version="v3.product-contract.1",
            certification_level="failed",
            certified=False,
            checks={"safety_case_link_included": False},
            findings=["safety case link missing"],
            limitations=["Certification is platform/workflow certification only."],
            certified_at=datetime(2026, 6, 5, 12, tzinfo=UTC),
            metadata={},
        )

    monkeypatch.setattr(
        "molecule_ranker.v3.discover.certify_v3_result_bundle",
        fail_certification,
    )

    result = run_v3_discover(
        V3DiscoverRequest(
            disease="Parkinson disease",
            mode="mocked",
            output_dir=tmp_path,
        )
    )

    assert result.validation_passed is True
    assert result.certification_passed is False
    assert result.status == "failed"
