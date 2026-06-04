from __future__ import annotations

from pathlib import Path

from molecule_ranker.platform.pilot_readiness import (
    PILOT_READINESS_VERSION,
    V19_PILOT_AREAS,
    build_pilot_readiness_report,
    build_support_bundle_manifest,
    pilot_error_message,
    summarize_job_control_capabilities,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v19_pilot_readiness_covers_enterprise_operational_focus() -> None:
    report = build_pilot_readiness_report(ROOT)

    assert PILOT_READINESS_VERSION == "pilot-readiness.v1.9"
    assert report["version"] == "2.2.0"
    assert report["ready"] is True
    assert len(V19_PILOT_AREAS) == 15
    assert {area.area_id for area in V19_PILOT_AREAS} == {
        "usability-polish",
        "performance-optimization",
        "reliability-hardening",
        "operational-readiness",
        "pilot-onboarding",
        "admin-support-workflows",
        "better-error-messages",
        "job-retry-resume-cancel",
        "dashboard-workflow-improvements",
        "dataset-artifact-migration-safety",
        "deployment-diagnostics",
        "monitoring-alerting",
        "pilot-feedback-capture",
        "support-bundle-generation",
        "pre-v2-readiness-validation",
    }
    assert all(area["status"] == "pass" for area in report["areas"])


def test_v19_readiness_preserves_scientific_and_codex_boundaries() -> None:
    report = build_pilot_readiness_report(ROOT)

    assert report["scope_boundary"] == "enterprise_internal_pilot_readiness"
    assert report["science_capability_expansion"] is False
    assert report["codex_boundary"] == {
        "codex_outputs_are_not_evidence": True,
        "codex_outputs_are_not_assay_results": True,
        "codex_outputs_are_not_molecules": True,
        "codex_outputs_are_not_scores": True,
        "codex_outputs_are_not_benchmark_results": True,
        "codex_outputs_are_not_decisions": True,
    }
    assert "no medical advice" in report["scientific_integrity_constraints"]
    assert "no generated molecules presented as validated actives" in report[
        "scientific_integrity_constraints"
    ]


def test_support_bundle_manifest_excludes_sensitive_and_cache_paths(tmp_path: Path) -> None:
    safe_log = tmp_path / "pilot.log"
    safe_log.write_text("request_id=req-1 api_key=secret-token-value\n")
    cache_file = tmp_path / ".cache" / "molecule-ranker" / "cached.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"token": "secret-token-value"}\n')

    manifest = build_support_bundle_manifest(
        ROOT,
        extra_files=[safe_log, cache_file],
    )

    assert manifest["bundle_version"] == PILOT_READINESS_VERSION
    assert manifest["includes_file_contents"] is False
    assert manifest["includes_environment_variables"] is False
    assert manifest["includes_cache_files"] is False
    paths = {item["path"] for item in manifest["files"]}
    assert safe_log.as_posix() in paths
    assert cache_file.as_posix() not in paths
    assert all("secret-token-value" not in str(item) for item in manifest["files"])


def test_job_control_and_error_message_summaries_are_pilot_safe() -> None:
    controls = summarize_job_control_capabilities()
    error = pilot_error_message(
        "job_cancel_pending",
        detail="Worker will stop at the next checkpoint. api_key=secret-token-value",
        remediation="Refresh the job page or contact support with the request ID.",
    )

    assert controls == {
        "retry": "failed_jobs_can_be_requeued_from_redacted_snapshots",
        "resume": "running_jobs_use_checkpoint_metadata_when_handlers_support_it",
        "cancel": "queued_jobs_cancel_immediately_running_jobs_mark_cancel_requested",
        "audit": "job_control_events_are_written_to_audit_log",
    }
    assert error["code"] == "job_cancel_pending"
    assert "api_key" not in error["detail"]
    assert "secret-token-value" not in error["detail"]
    assert "request ID" in error["remediation"]
