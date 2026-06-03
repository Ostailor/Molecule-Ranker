from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker import __version__
from molecule_ranker.pilot.feedback import PilotFeedbackStore
from molecule_ranker.pilot.ops_observability import INTERNAL_TARGETS, build_ops_metrics
from molecule_ranker.pilot.readiness import PilotReadinessConfig, run_pilot_readiness_audit
from molecule_ranker.pilot.support_bundle import generate_support_bundle_manifest
from molecule_ranker.release.checks import run_release_checks

ReleaseGateStatus = Literal["pass", "warn", "fail"]

PILOT_RELEASE_GATE_VERSION = "pilot-release-gate.v1.9"
DEFAULT_EVIDENCE_DIR = Path(".molecule-ranker/pilot/release_gate")
REQUIRED_PILOT_DOCS = (
    "docs/pilot/pilot_setup.md",
    "docs/pilot/pilot_success_criteria.md",
    "docs/pilot/onboarding_checklist.md",
    "docs/pilot/admin_checklist.md",
    "docs/pilot/user_training.md",
    "docs/pilot/support_process.md",
    "docs/pilot/troubleshooting_decision_tree.md",
    "docs/pilot/performance_tuning.md",
    "docs/pilot/reliability_operations.md",
    "docs/pilot/pilot_exit_review.md",
    "docs/pilot_sdk.md",
    "docs/v1.9-pilot-readiness.md",
)
REQUIRED_RUNBOOKS = (
    "docs/runbooks/deployment.md",
    "docs/runbooks/deployment_diagnostics.md",
    "docs/runbooks/monitoring_alerting.md",
    "docs/runbooks/pilot_onboarding.md",
    "docs/runbooks/support_bundle.md",
    "docs/runbooks/worker_operations.md",
)


@dataclass(frozen=True)
class PilotReleaseGateConfig:
    root_dir: Path = Path(".")
    evidence_dir: Path | None = None
    database_path: Path | None = None
    database_url: str | None = None
    job_failure_rate_threshold: float = float(INTERNAL_TARGETS["job_failure_rate"])
    median_dashboard_response_threshold_seconds: float = 2.0
    run_release_commands: bool = False

    @classmethod
    def synthetic(cls, **overrides: Any) -> PilotReleaseGateConfig:
        return cls(**overrides)


def run_pilot_release_gate(
    config: PilotReleaseGateConfig | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    active = config or PilotReleaseGateConfig()
    if overrides:
        active = PilotReleaseGateConfig(
            **{
                **active.__dict__,
                **overrides,
            }
        )
    root = active.root_dir.resolve()
    evidence_dir = _evidence_dir(root, active.evidence_dir)
    release_report = run_release_checks(root, run_commands=active.run_release_commands)
    readiness = _readiness_report(active, root, evidence_dir)
    support_manifest = _support_bundle_manifest(root, evidence_dir)
    ops_metrics = _ops_metrics(active, root, evidence_dir)
    success_metrics = _pilot_success_metrics(active, root, evidence_dir, ops_metrics)

    checks = [
        _version_check(),
        _evidence_check(
            "full_test_suite_pass_marker",
            "Full test suite pass marker",
            root,
            evidence_dir,
            ("full_test_suite_passed.json", "tests-passed.json"),
            fallback_paths=(root / ".molecule-ranker" / "release" / "tests-passed.json",),
        ),
        _release_status_check(
            "release_validation_passed",
            "Release validation passed",
            release_report,
        ),
        _release_child_check(
            "security_validation_passed",
            "Security validation passed",
            release_report,
            "security_audit",
            evidence_dir,
            ("security_validation.json",),
        ),
        _release_child_check(
            "guardrail_benchmark_passed",
            "Guardrail benchmark passed",
            release_report,
            "guardrail_audit",
            evidence_dir,
            ("guardrail_benchmark.json", "guardrail_validation.json"),
        ),
        _performance_profile_check(root, evidence_dir),
        _readiness_check(readiness),
        _migration_dry_run_check(root, evidence_dir),
        _evidence_check(
            "backup_verification_passed",
            "Backup verification passed",
            root,
            evidence_dir,
            ("backup_verification.json",),
        ),
        _support_bundle_check(root, evidence_dir, support_manifest),
        _evidence_check(
            "pilot_demo_runs",
            "Pilot demo runs",
            root,
            evidence_dir,
            ("pilot_demo.json",),
        ),
        _required_paths_check("docs_exist", "Pilot docs exist", root, REQUIRED_PILOT_DOCS),
        _required_paths_check("runbooks_exist", "Pilot runbooks exist", root, REQUIRED_RUNBOOKS),
        _evidence_check(
            "docker_build_works",
            "Docker build works",
            root,
            evidence_dir,
            ("docker_build.json",),
        ),
        _release_child_check(
            "no_critical_todo_markers",
            "No critical TODO markers",
            release_report,
            "no_todo_critical_markers",
            evidence_dir,
            (),
        ),
        _release_child_check(
            "no_plaintext_secrets",
            "No plaintext secrets",
            release_report,
            "no_plaintext_secrets",
            evidence_dir,
            (),
        ),
        _cache_exposure_check(readiness, support_manifest),
        _pilot_success_metrics_check(success_metrics),
    ]
    status: ReleaseGateStatus = "fail" if any(check["status"] == "fail" for check in checks) else (
        "warn" if any(check["status"] == "warn" for check in checks) else "pass"
    )
    return {
        "gate_id": f"pilot-release-gate-{uuid.uuid4().hex[:16]}",
        "gate_version": PILOT_RELEASE_GATE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "status": status,
        "root_dir": root.as_posix(),
        "evidence_dir": evidence_dir.as_posix(),
        "checks": checks,
        "summary": {
            "pass": sum(1 for check in checks if check["status"] == "pass"),
            "warn": sum(1 for check in checks if check["status"] == "warn"),
            "fail": sum(1 for check in checks if check["status"] == "fail"),
        },
        "pilot_success_metrics": success_metrics,
        "scientific_boundaries": {
            "generated_molecules_are_computational_hypotheses": True,
            "codex_output_is_not_evidence_or_decision": True,
            "evaluation_outputs_are_artifacts_not_biomedical_evidence": True,
        },
    }


def build_pilot_exit_review(
    *,
    project_id: str,
    config: PilotReleaseGateConfig | None = None,
) -> dict[str, Any]:
    active = config or PilotReleaseGateConfig()
    gate = run_pilot_release_gate(active)
    root = active.root_dir.resolve()
    feedback = PilotFeedbackStore(root).list(project_id=project_id, limit=10_000)
    decision = "continue" if gate["status"] == "pass" else "extend"
    if any(_critical_failure(check) for check in gate["checks"]):
        decision = "stop"
    return {
        "review_id": f"pilot-exit-review-{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now(UTC).isoformat(),
        "project_id": project_id,
        "decision": decision,
        "release_gate_status": gate["status"],
        "release_gate_summary": gate["summary"],
        "feedback_count": len(feedback),
        "feedback_status_counts": _feedback_status_counts(feedback),
        "success_metrics": gate["pilot_success_metrics"],
        "remaining_risks": [
            check["message"] for check in gate["checks"] if check["status"] != "pass"
        ],
        "boundary": "Pilot exit review is an operational rollout decision record.",
    }


def render_pilot_exit_review_markdown(review: dict[str, Any]) -> str:
    lines = [
        "# Pilot Exit Review",
        "",
        f"- Review ID: `{review['review_id']}`",
        f"- Project ID: `{review['project_id']}`",
        f"- Decision: `{review['decision']}`",
        f"- Release gate: `{review['release_gate_status']}`",
        f"- Feedback count: {review['feedback_count']}",
        "",
        "## Success Metrics",
        "",
    ]
    for metric in review["success_metrics"]:
        lines.append(f"- {metric['metric_id']}: {metric['status']} - {metric['message']}")
    if review["remaining_risks"]:
        lines.extend(["", "## Remaining Risks", ""])
        lines.extend(f"- {risk}" for risk in review["remaining_risks"])
    lines.append("")
    return "\n".join(lines)


def _version_check() -> dict[str, Any]:
    passed = __version__ == "2.0.0"
    return _check(
        "package_version_is_2_0_0",
        "pass" if passed else "fail",
        "Package version is 2.0.0." if passed else f"Package version is {__version__}.",
    )


def _release_status_check(
    check_id: str,
    title: str,
    release_report: dict[str, Any],
) -> dict[str, Any]:
    passed = release_report.get("status") == "pass"
    return _check(
        check_id,
        "pass" if passed else "fail",
        f"{title}." if passed else "Release validation report did not pass.",
        details={"summary": release_report.get("summary", {})},
    )


def _release_child_check(
    check_id: str,
    title: str,
    release_report: dict[str, Any],
    release_check_id: str,
    evidence_dir: Path,
    evidence_names: tuple[str, ...],
) -> dict[str, Any]:
    evidence = _first_evidence(evidence_dir, evidence_names)
    if evidence is not None:
        return _evidence_payload_check(check_id, title, evidence)
    child = _release_check(release_report, release_check_id)
    passed = child is not None and child.get("status") == "pass"
    return _check(
        check_id,
        "pass" if passed else "fail",
        f"{title}." if passed else f"{title} did not pass.",
        details={"release_check": child or {}},
    )


def _evidence_check(
    check_id: str,
    title: str,
    root: Path,
    evidence_dir: Path,
    evidence_names: tuple[str, ...],
    *,
    fallback_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    del root
    evidence = _first_evidence(evidence_dir, evidence_names)
    if evidence is not None:
        return _evidence_payload_check(check_id, title, evidence)
    for path in fallback_paths:
        if path.exists():
            return _evidence_payload_check(check_id, title, _load_json(path), path=path)
    return _check(check_id, "fail", f"{title} evidence is missing.")


def _performance_profile_check(root: Path, evidence_dir: Path) -> dict[str, Any]:
    candidates = (
        evidence_dir / "performance_profile.json",
        evidence_dir / "performance_report.json",
        root / "performance_report.json",
        root / ".molecule-ranker" / "performance" / "performance_report.json",
    )
    for path in candidates:
        if path.exists():
            payload = _load_json(path)
            if payload is None:
                return _check(
                    "performance_profile_generated",
                    "fail",
                    "Performance profile exists but could not be parsed.",
                    details={"path": path.as_posix()},
                )
            if _performance_profile_payload_passed(payload):
                return _check(
                    "performance_profile_generated",
                    "pass",
                    "Performance profile generated.",
                    details={"path": path.as_posix()},
                )
            return _evidence_payload_check(
                "performance_profile_generated",
                "Performance profile generated",
                payload,
                path=path,
            )
    return _check("performance_profile_generated", "fail", "Performance profile is missing.")


def _migration_dry_run_check(root: Path, evidence_dir: Path) -> dict[str, Any]:
    candidates = (
        evidence_dir / "migration_dry_run.json",
        evidence_dir / "migration_manifest.json",
        root / "migration_manifest.json",
        root / ".molecule-ranker" / "migrations" / "migration_manifest.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        payload = _load_json(path)
        if payload is None:
            return _check(
                "migration_dry_run_passed",
                "fail",
                "Migration dry-run report exists but could not be parsed.",
                details={"path": path.as_posix()},
            )
        if _migration_dry_run_payload_passed(payload):
            return _check(
                "migration_dry_run_passed",
                "pass",
                "Migration dry-run passed.",
                details={"path": path.as_posix(), "summary": payload.get("summary", {})},
            )
        return _evidence_payload_check(
            "migration_dry_run_passed",
            "Migration dry-run passed",
            payload,
            path=path,
        )
    return _check("migration_dry_run_passed", "fail", "Migration dry-run evidence is missing.")


def _readiness_check(readiness: dict[str, Any]) -> dict[str, Any]:
    passed = int(readiness.get("failed_count", 1)) == 0
    return _check(
        "readiness_report_passed",
        "pass" if passed else "fail",
        "Pilot readiness report passed." if passed else "Pilot readiness report has failures.",
        details={
            "failed_count": readiness.get("failed_count"),
            "warning_count": readiness.get("warning_count"),
        },
    )


def _support_bundle_check(
    root: Path,
    evidence_dir: Path,
    support_manifest: dict[str, Any],
) -> dict[str, Any]:
    evidence = _first_evidence(
        evidence_dir,
        ("support_bundle.json", "support_bundle_manifest.json"),
    )
    if evidence is not None:
        return _evidence_payload_check(
            "support_bundle_generation_passed",
            "Support bundle generation passed",
            evidence,
        )
    if (
        (root / "support_bundle.zip").exists()
        or (root / ".molecule-ranker/support-bundles").exists()
    ):
        return _check(
            "support_bundle_generation_passed",
            "pass",
            "Support bundle output exists.",
        )
    if support_manifest.get("bundle_version"):
        return _check(
            "support_bundle_generation_passed",
            "pass",
            "Support bundle manifest can be generated.",
        )
    return _check("support_bundle_generation_passed", "fail", "Support bundle evidence is missing.")


def _required_paths_check(
    check_id: str,
    title: str,
    root: Path,
    required: tuple[str, ...],
) -> dict[str, Any]:
    missing = [path for path in required if not (root / path).exists()]
    return _check(
        check_id,
        "pass" if not missing else "fail",
        f"{title}." if not missing else f"{title} are missing.",
        details={"missing": missing},
    )


def _cache_exposure_check(
    readiness: dict[str, Any],
    support_manifest: dict[str, Any],
) -> dict[str, Any]:
    readiness_pass = _readiness_check_status(readiness, "cache_files_not_exposed") == "pass"
    support_pass = support_manifest.get("includes_cache_files") is False or bool(
        support_manifest.get("bundle_version")
    )
    passed = readiness_pass and support_pass
    return _check(
        "no_cache_exposure",
        "pass" if passed else "fail",
        "Cache files are not exposed." if passed else "Cache exposure guard failed.",
        details={"readiness_pass": readiness_pass, "support_manifest_pass": support_pass},
    )


def _pilot_success_metrics_check(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [metric for metric in metrics if metric["status"] != "pass"]
    return _check(
        "pilot_success_metrics_met",
        "pass" if not failed else "fail",
        "Pilot success metrics are met." if not failed else "Pilot success metrics are not met.",
        details={"failed_metrics": failed},
    )


def _pilot_success_metrics(
    config: PilotReleaseGateConfig,
    root: Path,
    evidence_dir: Path,
    ops_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = _first_evidence(evidence_dir, ("pilot_success_metrics.json",))
    evidence_metrics = evidence if isinstance(evidence, dict) else {}
    job_failure_rate = float(
        _nested(
            ops_metrics,
            ("job_monitoring", "failure_rate"),
            evidence_metrics.get("job_failure_rate", 1.0),
        )
    )
    median_dashboard_response = float(
        evidence_metrics.get("median_dashboard_response_seconds", 0.0)
    )
    feedback_count = len(PilotFeedbackStore(root).list(limit=10_000))
    feedback_collected = bool(evidence_metrics.get("feedback_collected")) or feedback_count > 0
    metrics = [
        _metric(
            "golden_workflow_without_maintainer",
            bool(evidence_metrics.get("golden_workflow_without_maintainer")),
            "User completed golden workflow without maintainer intervention.",
        ),
        _metric(
            "no_critical_guardrail_failures",
            bool(evidence_metrics.get("no_critical_guardrail_failures", True)),
            "No critical guardrail failures were observed.",
        ),
        _metric(
            "job_failure_rate_below_threshold",
            job_failure_rate < config.job_failure_rate_threshold,
            f"Job failure rate {job_failure_rate:.3f} is below threshold.",
        ),
        _metric(
            "median_dashboard_response_below_threshold",
            median_dashboard_response < config.median_dashboard_response_threshold_seconds,
            f"Median dashboard response {median_dashboard_response:.3f}s is below threshold.",
        ),
        _metric(
            "support_bundle_generated",
            bool(evidence_metrics.get("support_bundle_generated")),
            "Support bundle was generated successfully.",
        ),
        _metric(
            "feedback_collected",
            feedback_collected,
            "Pilot feedback was collected.",
        ),
        _metric(
            "synthetic_campaign_evaluation_completed",
            bool(evidence_metrics.get("synthetic_campaign_evaluation_completed")),
            "At least one end-to-end synthetic campaign/evaluation workflow completed.",
        ),
    ]
    return metrics


def _readiness_report(
    config: PilotReleaseGateConfig,
    root: Path,
    evidence_dir: Path,
) -> dict[str, Any]:
    evidence = _first_evidence(
        evidence_dir,
        ("pilot_readiness_report.json", "readiness_report.json"),
    )
    if isinstance(evidence, dict):
        return evidence
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=root,
            database_path=config.database_path,
            database_url=config.database_url,
        )
    )
    return report.model_dump(mode="json")


def _support_bundle_manifest(root: Path, evidence_dir: Path) -> dict[str, Any]:
    evidence = _first_evidence(evidence_dir, ("support_bundle_manifest.json",))
    if isinstance(evidence, dict):
        return evidence
    try:
        return generate_support_bundle_manifest(root)
    except Exception:
        return {}


def _ops_metrics(
    config: PilotReleaseGateConfig,
    root: Path,
    evidence_dir: Path,
) -> dict[str, Any]:
    evidence = _first_evidence(evidence_dir, ("ops_metrics.json", "metrics.json"))
    if isinstance(evidence, dict):
        return evidence
    try:
        return build_ops_metrics(
            root_dir=root,
            database_url=config.database_url,
            db_path=config.database_path,
        )
    except Exception:
        return {}


def _first_evidence(evidence_dir: Path, names: tuple[str, ...]) -> Any | None:
    for name in names:
        path = evidence_dir / name
        if path.exists():
            return _load_json(path)
    return None


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _evidence_payload_check(
    check_id: str,
    title: str,
    payload: Any,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    passed = _payload_passed(payload)
    details: dict[str, Any] = {"path": path.as_posix()} if path else {}
    if isinstance(payload, dict):
        details.update(
            {
                key: payload.get(key)
                for key in ("status", "ok", "passed", "failed_count", "summary")
                if key in payload
            }
        )
    return _check(
        check_id,
        "pass" if passed else "fail",
        f"{title}." if passed else f"{title} evidence did not pass.",
        details=details,
    )


def _payload_passed(payload: Any) -> bool:
    if payload is True:
        return True
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or payload.get("result") or "").lower()
    if status in {"pass", "passed", "ok", "success", "succeeded", "completed"}:
        return True
    if status in {"fail", "failed", "error", "blocked"}:
        return False
    if payload.get("ok") is True or payload.get("passed") is True:
        return True
    if payload.get("failed_count") == 0:
        return True
    summary = payload.get("summary")
    if isinstance(summary, dict) and summary.get("fail") == 0:
        return True
    return False


def _performance_profile_payload_passed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    measurements = payload.get("measurements")
    if not isinstance(measurements, dict) or not measurements:
        return False
    required = {
        "ranking_pipeline",
        "literature_retrieval",
        "generation",
        "developability",
        "dashboard_response",
        "api_response",
        "job_queue_wait",
        "artifact_read",
        "artifact_write",
    }
    if not required.issubset(measurements):
        return False
    if payload.get("live_apis_enabled") is True:
        return False
    return bool(payload.get("workflow") or payload.get("profile_id") or payload.get("created_at"))


def _migration_dry_run_payload_passed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("dry_run") is not True:
        return False
    if not payload.get("manifest_id") and not payload.get("migration_id"):
        return False
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return False
    return int(summary.get("artifact_count", 0)) >= 0


def _release_check(report: dict[str, Any], check_id: str) -> dict[str, Any] | None:
    for check in report.get("checks", []):
        if check.get("check_id") == check_id:
            return check
    return None


def _readiness_check_status(readiness: dict[str, Any], check_id: str) -> str | None:
    for check in readiness.get("checks", []):
        if check.get("check_id") == check_id:
            return str(check.get("status"))
    return None


def _check(
    check_id: str,
    status: ReleaseGateStatus,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "message": message,
        "details": details or {},
    }


def _metric(metric_id: str, passed: bool, message: str) -> dict[str, Any]:
    return {"metric_id": metric_id, "status": "pass" if passed else "fail", "message": message}


def _nested(payload: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _feedback_status_counts(feedback: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in feedback:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _critical_failure(check: dict[str, Any]) -> bool:
    if check.get("status") == "pass":
        return False
    return bool(re.search(r"secret|guardrail|cache|version|readiness", str(check.get("check_id"))))


def _evidence_dir(root: Path, evidence_dir: Path | None) -> Path:
    path = evidence_dir or DEFAULT_EVIDENCE_DIR
    return path if path.is_absolute() else root / path


__all__ = [
    "PILOT_RELEASE_GATE_VERSION",
    "PilotReleaseGateConfig",
    "build_pilot_exit_review",
    "render_pilot_exit_review_markdown",
    "run_pilot_release_gate",
]
