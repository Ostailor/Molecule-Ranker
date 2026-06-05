from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.pilot.release_gate import (
    PilotReleaseGateConfig,
    build_pilot_exit_review,
    run_pilot_release_gate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_passes_synthetic_setup(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)

    report = run_pilot_release_gate(
        PilotReleaseGateConfig.synthetic(root_dir=ROOT, evidence_dir=evidence)
    )

    assert report["status"] == "pass"
    assert _check(report, "package_version_is_2_6_0")["status"] == "pass"
    assert _check(report, "pilot_success_metrics_met")["status"] == "pass"


def test_release_gate_missing_docs_fail(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    root = tmp_path / "missing-docs-root"
    root.mkdir()

    report = run_pilot_release_gate(
        PilotReleaseGateConfig.synthetic(root_dir=root, evidence_dir=evidence)
    )

    assert report["status"] == "fail"
    assert _check(report, "docs_exist")["status"] == "fail"
    assert _check(report, "runbooks_exist")["status"] == "fail"


def test_release_gate_failed_guardrail_fails(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    _write(evidence / "guardrail_benchmark.json", {"status": "fail"})

    report = run_pilot_release_gate(
        PilotReleaseGateConfig.synthetic(root_dir=ROOT, evidence_dir=evidence)
    )

    assert report["status"] == "fail"
    assert _check(report, "guardrail_benchmark_passed")["status"] == "fail"


def test_release_gate_accepts_real_profile_and_migration_manifest_shapes(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    (evidence / "performance_profile.json").unlink()
    (evidence / "migration_dry_run.json").unlink()
    _write(
        evidence / "performance_report.json",
        {
            "profile_id": "profile-test",
            "created_at": "2026-06-02T00:00:00Z",
            "workflow": "golden",
            "live_apis_enabled": False,
            "measurements": {
                "ranking_pipeline": {},
                "literature_retrieval": {},
                "generation": {},
                "developability": {},
                "dashboard_response": {},
                "api_response": {},
                "job_queue_wait": {},
                "artifact_read": {},
                "artifact_write": {},
            },
        },
    )
    _write(
        evidence / "migration_manifest.json",
        {
            "manifest_id": "migration-test",
            "dry_run": True,
            "summary": {"artifact_count": 2, "would_migrate_count": 1},
        },
    )

    report = run_pilot_release_gate(
        PilotReleaseGateConfig.synthetic(root_dir=ROOT, evidence_dir=evidence)
    )

    assert report["status"] == "pass"
    assert _check(report, "performance_profile_generated")["status"] == "pass"
    assert _check(report, "migration_dry_run_passed")["status"] == "pass"


def test_release_gate_cli_json_and_exit_review(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    runner = CliRunner()

    gate = runner.invoke(
        app,
        [
            "pilot",
            "release-gate",
            "--json",
            "--root",
            str(ROOT),
            "--evidence-dir",
            str(evidence),
        ],
    )
    exit_review = build_pilot_exit_review(
        project_id="synthetic-project",
        config=PilotReleaseGateConfig.synthetic(root_dir=ROOT, evidence_dir=evidence),
    )

    assert gate.exit_code == 0, gate.output
    assert json.loads(gate.output)["status"] == "pass"
    assert exit_review["project_id"] == "synthetic-project"
    assert exit_review["decision"] == "continue"


def _passing_evidence(tmp_path: Path) -> Path:
    evidence = tmp_path / "release_gate_evidence"
    evidence.mkdir()
    _write(evidence / "full_test_suite_passed.json", {"status": "pass"})
    _write(evidence / "security_validation.json", {"status": "pass"})
    _write(evidence / "guardrail_benchmark.json", {"status": "pass"})
    _write(evidence / "performance_profile.json", {"status": "pass", "workflow": "golden"})
    _write(
        evidence / "pilot_readiness_report.json",
        {
            "failed_count": 0,
            "warning_count": 0,
            "checks": [{"check_id": "cache_files_not_exposed", "status": "pass"}],
        },
    )
    _write(evidence / "migration_dry_run.json", {"status": "pass", "dry_run": True})
    _write(evidence / "backup_verification.json", {"status": "pass"})
    _write(
        evidence / "support_bundle_manifest.json",
        {"status": "pass", "bundle_version": "synthetic", "includes_cache_files": False},
    )
    _write(evidence / "pilot_demo.json", {"status": "pass"})
    _write(evidence / "docker_build.json", {"status": "pass"})
    _write(
        evidence / "ops_metrics.json",
        {"job_monitoring": {"failure_rate": 0.01}},
    )
    _write(
        evidence / "pilot_success_metrics.json",
        {
            "golden_workflow_without_maintainer": True,
            "no_critical_guardrail_failures": True,
            "job_failure_rate": 0.01,
            "median_dashboard_response_seconds": 0.5,
            "support_bundle_generated": True,
            "feedback_collected": True,
            "synthetic_campaign_evaluation_completed": True,
        },
    )
    return evidence


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _check(report: dict, check_id: str) -> dict:
    return next(check for check in report["checks"] if check["check_id"] == check_id)
