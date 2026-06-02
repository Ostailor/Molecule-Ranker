from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.guardrails import redact_secrets

PILOT_READINESS_VERSION = "pilot-readiness.v1.9"
SUPPORT_BUNDLE_VERSION = PILOT_READINESS_VERSION

SCIENTIFIC_INTEGRITY_CONSTRAINTS = (
    "no medical advice",
    "no patient treatment guidance",
    "no dosage",
    "no synthesis instructions",
    "no lab protocols",
    "no fabricated evidence",
    "no fabricated assay results",
    "no fake citations",
    "no Codex-generated biomedical truth",
    "no generated molecules presented as validated actives",
)

CODEX_BOUNDARY = {
    "codex_outputs_are_not_evidence": True,
    "codex_outputs_are_not_assay_results": True,
    "codex_outputs_are_not_molecules": True,
    "codex_outputs_are_not_scores": True,
    "codex_outputs_are_not_benchmark_results": True,
    "codex_outputs_are_not_decisions": True,
}

SENSITIVE_PATH_PARTS = {
    ".cache",
    ".env",
    ".molecule-ranker/cache",
    "__pycache__",
}
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|secret|service[_-]?token|token)\s*="
    r"\s*[^,\s;]+"
)


@dataclass(frozen=True)
class PilotReadinessArea:
    area_id: str
    title: str
    required_evidence: tuple[str, ...]

    def as_dict(self, *, root_dir: Path) -> dict[str, Any]:
        missing = [path for path in self.required_evidence if not (root_dir / path).exists()]
        return {
            "area_id": self.area_id,
            "title": self.title,
            "required_evidence": list(self.required_evidence),
            "status": "pass" if not missing else "missing_evidence",
            "missing_evidence": missing,
        }


V19_PILOT_AREAS: tuple[PilotReadinessArea, ...] = (
    PilotReadinessArea(
        "usability-polish",
        "Hosted dashboard, user docs, and workflow language are ready for internal pilots.",
        (
            "docs/user/dashboard.md",
            "docs/user/overview.md",
            "molecule_ranker/web/templates/base.html",
        ),
    ),
    PilotReadinessArea(
        "performance-optimization",
        "Performance-sensitive pilot paths expose observable timings and bounded workloads.",
        (
            "molecule_ranker/platform/observability.py",
            "molecule_ranker/platform/jobs.py",
            "docs/runbooks/worker_operations.md",
        ),
    ),
    PilotReadinessArea(
        "reliability-hardening",
        "Reliability paths cover health, readiness, backup, restore, and worker failure modes.",
        (
            "molecule_ranker/platform/readiness.py",
            "molecule_ranker/platform/backup.py",
            "tests/test_platform_backup.py",
        ),
    ),
    PilotReadinessArea(
        "operational-readiness",
        "Operators have production configuration, release, and incident runbooks.",
        (
            "docs/runbooks/production_config.md",
            "docs/runbooks/release_process.md",
            "docs/runbooks/security_incidents.md",
        ),
    ),
    PilotReadinessArea(
        "pilot-onboarding",
        "Pilot onboarding has a non-science-scoping checklist and handoff guide.",
        ("docs/runbooks/pilot_onboarding.md",),
    ),
    PilotReadinessArea(
        "admin-support-workflows",
        "Admin and support workflows are documented without exposing credentials.",
        (
            "docs/admin/support_workflows.md",
            "docs/admin/users_and_roles.md",
            "docs/admin/audit_logs.md",
        ),
    ),
    PilotReadinessArea(
        "better-error-messages",
        "Pilot-safe error messages include redacted details and remediation hints.",
        (
            "molecule_ranker/platform/pilot_readiness.py",
            "molecule_ranker/server/app.py",
            "docs/runbooks/troubleshooting.md",
        ),
    ),
    PilotReadinessArea(
        "job-retry-resume-cancel",
        "Job retry, resume, and cancel expectations are explicit for queued and running jobs.",
        (
            "molecule_ranker/platform/jobs.py",
            "molecule_ranker/workers/base.py",
            "docs/runbooks/worker_operations.md",
        ),
    ),
    PilotReadinessArea(
        "dashboard-workflow-improvements",
        "Dashboard workflows keep research boundaries visible across pilot pages.",
        (
            "molecule_ranker/web/templates/base.html",
            "molecule_ranker/web/views.py",
            "tests/test_web_dashboard.py",
        ),
    ),
    PilotReadinessArea(
        "dataset-artifact-migration-safety",
        "Dataset and artifact migration surfaces are checked before pilot upgrades.",
        (
            "molecule_ranker/platform/migrations.py",
            "docs/admin/artifact_storage.md",
            "docs/runbooks/backup_restore.md",
        ),
    ),
    PilotReadinessArea(
        "deployment-diagnostics",
        "Deployment diagnostics cover version, readiness, metrics, and configuration checks.",
        (
            "docs/runbooks/deployment_diagnostics.md",
            "docs/runbooks/deployment.md",
            "molecule_ranker/platform/readiness.py",
        ),
    ),
    PilotReadinessArea(
        "monitoring-alerting",
        "Monitoring and alerting signals are documented for pilot operations.",
        (
            "docs/runbooks/monitoring_alerting.md",
            "molecule_ranker/platform/observability.py",
            "tests/test_platform_observability.py",
        ),
    ),
    PilotReadinessArea(
        "pilot-feedback-capture",
        "Pilot feedback capture is separated from evidence, assay results, and decisions.",
        (
            "docs/user/pilot_feedback.md",
            "molecule_ranker/review/feedback.py",
            "tests/test_review_feedback.py",
        ),
    ),
    PilotReadinessArea(
        "support-bundle-generation",
        "Support bundles list redacted diagnostic artifacts without secrets or cache contents.",
        ("docs/runbooks/support_bundle.md", "molecule_ranker/platform/pilot_readiness.py"),
    ),
    PilotReadinessArea(
        "pre-v2-readiness-validation",
        "Pre-V2.0 readiness validates operational maturity without adding science capabilities.",
        ("docs/v1.9-pilot-readiness.md", "molecule_ranker/release/checks.py"),
    ),
)


def build_pilot_readiness_report(root_dir: str | Path = ".") -> dict[str, Any]:
    root = Path(root_dir).resolve()
    areas = [area.as_dict(root_dir=root) for area in V19_PILOT_AREAS]
    return {
        "name": "molecule-ranker",
        "version": __version__,
        "pilot_readiness_version": PILOT_READINESS_VERSION,
        "scope_boundary": "enterprise_internal_pilot_readiness",
        "science_capability_expansion": False,
        "ready": all(area["status"] == "pass" for area in areas),
        "areas": areas,
        "codex_boundary": dict(CODEX_BOUNDARY),
        "scientific_integrity_constraints": list(SCIENTIFIC_INTEGRITY_CONSTRAINTS),
    }


def build_support_bundle_manifest(
    root_dir: str | Path = ".",
    *,
    extra_files: list[str | Path] | None = None,
) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    candidates = [
        root / "README.md",
        root / "pyproject.toml",
        root / "docs/v1.9-pilot-readiness.md",
        root / "docs/runbooks/deployment_diagnostics.md",
        root / "docs/runbooks/monitoring_alerting.md",
        root / "docs/runbooks/troubleshooting.md",
    ]
    candidates.extend(Path(path) for path in extra_files or [])
    files = [_support_file_entry(path) for path in candidates if _is_support_bundle_safe(path)]
    return {
        "bundle_version": SUPPORT_BUNDLE_VERSION,
        "root_dir": root.as_posix(),
        "includes_file_contents": False,
        "includes_environment_variables": False,
        "includes_cache_files": False,
        "files": files,
        "excluded": [
            "environment variables",
            "cache files",
            "API keys",
            "service tokens",
            "credentials",
            "plaintext secrets",
        ],
    }


def summarize_job_control_capabilities() -> dict[str, str]:
    return {
        "retry": "failed_jobs_can_be_requeued_from_redacted_snapshots",
        "resume": "running_jobs_use_checkpoint_metadata_when_handlers_support_it",
        "cancel": "queued_jobs_cancel_immediately_running_jobs_mark_cancel_requested",
        "audit": "job_control_events_are_written_to_audit_log",
    }


def pilot_error_message(
    code: str,
    *,
    detail: str,
    remediation: str,
    request_id: str | None = None,
) -> dict[str, str]:
    message = {
        "code": code,
        "detail": _redact_pilot_text(detail),
        "remediation": _redact_pilot_text(remediation),
    }
    if request_id:
        message["request_id"] = _redact_pilot_text(request_id)
    return message


def _support_file_entry(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": resolved.as_posix(),
        "exists": resolved.exists(),
        "size_bytes": resolved.stat().st_size if resolved.exists() and resolved.is_file() else 0,
    }


def _is_support_bundle_safe(path: Path) -> bool:
    text = path.as_posix()
    lowered = text.lower()
    if any(part in lowered for part in SENSITIVE_PATH_PARTS):
        return False
    if path.name.startswith(".env"):
        return False
    return True


def _redact_pilot_text(value: str) -> str:
    return SENSITIVE_ASSIGNMENT_RE.sub("[REDACTED]", redact_secrets(value))


__all__ = [
    "PILOT_READINESS_VERSION",
    "V19_PILOT_AREAS",
    "build_pilot_readiness_report",
    "build_support_bundle_manifest",
    "pilot_error_message",
    "summarize_job_control_capabilities",
]
