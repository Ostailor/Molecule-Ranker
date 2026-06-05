from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from sqlalchemy import insert

from molecule_ranker import __version__
from molecule_ranker.pilot.readiness import PilotReadinessConfig, run_pilot_readiness_audit
from molecule_ranker.pilot.support_bundle import create_support_bundle
from molecule_ranker.platform.backup import create_platform_backup, verify_platform_backup
from molecule_ranker.platform.database import PlatformDatabase, artifact_records, project_workspaces
from molecule_ranker.platform.disaster_recovery import run_disaster_recovery_drill
from molecule_ranker.platform.isolation import run_isolation_audit
from molecule_ranker.platform.security_audit import run_security_audit
from molecule_ranker.platform.slo import generate_slo_report, write_slo_report
from molecule_ranker.server import create_app
from molecule_ranker.v2 import validate_v2_release_contracts
from molecule_ranker.validation.enterprise_golden import run_enterprise_golden_workflow
from molecule_ranker.validation.guardrail_audit import run_guardrail_audit

ReleaseGateStatus = Literal["pass", "fail"]

V2_RELEASE_GATE_VERSION = "v2.release-gate.1"
V2_RELEASE_GATE_JSON = "v2_release_gate.json"
V2_RELEASE_GATE_MARKDOWN = "v2_release_gate.md"

REQUIRED_V2_DOCS = (
    "docs/v2/index.md",
    "docs/v2/quickstart.md",
    "docs/v2/architecture.md",
    "docs/v2/security_model.md",
    "docs/v2/deployment.md",
    "docs/v2/admin_guide.md",
    "docs/v2/user_guide.md",
    "docs/v2/scientist_guide.md",
    "docs/v2/reviewer_guide.md",
    "docs/v2/operator_guide.md",
    "docs/v2/integration_guide.md",
    "docs/v2/codex_backbone.md",
    "docs/v2/data_governance.md",
    "docs/v2/validation_package.md",
    "docs/v2/backup_restore.md",
    "docs/v2/troubleshooting.md",
    "docs/v2/limitations.md",
    "docs/v2/release_notes.md",
)
REQUIRED_TRAINING_DOCS = (
    "docs/training/admin_training.md",
    "docs/training/scientist_training.md",
    "docs/training/reviewer_training.md",
    "docs/training/operator_training.md",
    "docs/training/integration_admin_training.md",
    "docs/training/codex_guardrails_training.md",
    "docs/training/generated_molecule_interpretation.md",
    "docs/training/model_prediction_interpretation.md",
    "docs/training/evaluation_interpretation.md",
)
REQUIRED_DEPLOYMENT_PATHS = (
    "deployment/docker-compose.enterprise.yml",
    "deployment/k8s/deployment.yaml",
    "deployment/k8s/service.yaml",
    "deployment/helm/Chart.yaml",
    "deployment/README.md",
    "deployment/hardening.md",
)
EXPENSIVE_EVIDENCE_CHECKS = {
    "enterprise_golden_workflow_passes",
    "red_team_suite_passes",
    "dr_drill_passes",
    "backup_verification_passes",
    "security_audit_passes",
    "isolation_audit_passes",
    "readiness_audit_passes",
    "guardrail_benchmark_passes",
    "support_bundle_redaction_passes",
    "slo_report_generated",
    "deployment_smoke_passes",
}
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|credential)\s*[:=]\s*"
    r"(?!\\$\\{|<|REDACTED|example|placeholder|change-me|changeme|not-a-secret)"
    r"[A-Za-z0-9_./:+-]{12,}"
)
CRITICAL_TODO_RE = re.compile(
    r"(?i)(?:#|//|<!--)\s*(?:todo\b.*\b(?:critical|blocker)|(?:critical|blocker)\b.*\btodo\b)"
)
BIOMEDICAL_FIXTURE_RE = re.compile(
    r"(?i)\b(?:parkinson|alzheimer|cancer|tumou?r|diabetes|hypertension)\b"
)


@dataclass(frozen=True)
class V2ReleaseGateConfig:
    root_dir: Path = Path(".")
    output_dir: Path = Path(".")
    evidence_dir: Path | None = None
    run_expensive_checks: bool = True


def run_v2_release_gate(config: V2ReleaseGateConfig | None = None) -> dict[str, Any]:
    active = config or V2ReleaseGateConfig()
    root = active.root_dir.resolve()
    output_dir = active.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / ".molecule-ranker" / "v2-release-gate"
    work_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = active.evidence_dir.resolve() if active.evidence_dir else None

    checks = [
        _check_version(),
        _check_contracts(),
        _check_api_v2(root),
        _evidence_or_run(
            "enterprise_golden_workflow_passes",
            "Enterprise golden workflow passes.",
            evidence_dir,
            lambda: _enterprise_golden_check(root, work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "guardrail_benchmark_passes",
            "Guardrail benchmark passes.",
            evidence_dir,
            lambda: _guardrail_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "red_team_suite_passes",
            "Red-team suite passes.",
            evidence_dir,
            lambda: _redteam_check(root),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "security_audit_passes",
            "Security audit passes.",
            evidence_dir,
            lambda: _security_audit_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "isolation_audit_passes",
            "Isolation audit passes.",
            evidence_dir,
            lambda: _isolation_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "readiness_audit_passes",
            "Readiness audit passes.",
            evidence_dir,
            lambda: _readiness_check(root, work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "dr_drill_passes",
            "Disaster recovery drill passes.",
            evidence_dir,
            lambda: _dr_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "backup_verification_passes",
            "Backup verification passes.",
            evidence_dir,
            lambda: _backup_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "support_bundle_redaction_passes",
            "Support bundle redaction passes.",
            evidence_dir,
            lambda: _support_bundle_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "slo_report_generated",
            "SLO report generated.",
            evidence_dir,
            lambda: _slo_check(work_dir),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "deployment_smoke_passes",
            "Deployment smoke passes.",
            evidence_dir,
            lambda: _deployment_check(root),
            active.run_expensive_checks,
        ),
        _required_paths_check("docs_exist", "V2 docs exist.", root, REQUIRED_V2_DOCS),
        _required_paths_check(
            "training_materials_exist",
            "Training materials exist.",
            root,
            REQUIRED_TRAINING_DOCS,
        ),
        _required_paths_check(
            "release_notes_generated",
            "Release notes generated.",
            root,
            ("docs/v2/release_notes.md",),
        ),
        _no_critical_todos_check(root),
        _no_fixture_biomedical_data_check(root),
        _no_plaintext_secrets_check(root),
    ]
    status: ReleaseGateStatus = (
        "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    )
    report = {
        "gate_id": f"v2-release-gate-{uuid.uuid4().hex[:16]}",
        "gate_version": V2_RELEASE_GATE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "status": status,
        "root_dir": str(root),
        "output_dir": str(output_dir),
        "evidence_dir": str(evidence_dir) if evidence_dir else None,
        "checks": checks,
        "summary": {
            "pass": sum(1 for check in checks if check["status"] == "pass"),
            "fail": sum(1 for check in checks if check["status"] == "fail"),
        },
        "boundaries": {
            "software_platform_release_gate": True,
            "not_clinical_validation": True,
            "not_regulatory_approval": True,
            "no_medical_or_procedural_guidance": True,
        },
    }
    write_v2_release_gate_outputs(report, output_dir=output_dir)
    return report


def write_v2_release_gate_outputs(report: dict[str, Any], *, output_dir: Path) -> dict[str, Path]:
    json_path = output_dir / V2_RELEASE_GATE_JSON
    markdown_path = output_dir / V2_RELEASE_GATE_MARKDOWN
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_v2_release_gate_markdown(report))
    return {"json": json_path, "markdown": markdown_path}


def render_v2_release_gate_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V2.0 Release Gate",
        "",
        f"- Status: `{report['status']}`",
        f"- Version: `{report['version']}`",
        f"- Gate ID: `{report['gate_id']}`",
        f"- Passed checks: {report['summary']['pass']}",
        f"- Failed checks: {report['summary']['fail']}",
        "",
        (
            "This is software/platform release evidence, not clinical validation "
            "or regulatory approval."
        ),
        "",
        "## Checks",
        "",
    ]
    for check in report["checks"]:
        lines.append(f"- `{check['status']}` `{check['check_id']}` - {check['message']}")
    return "\n".join(lines) + "\n"


def _evidence_or_run(
    check_id: str,
    pass_message: str,
    evidence_dir: Path | None,
    runner: Callable[[], dict[str, Any]],
    run_if_missing: bool,
) -> dict[str, Any]:
    marker = _evidence_marker(evidence_dir, check_id)
    if marker is not None:
        status = str(marker.get("status", "")).lower()
        return _check(
            check_id,
            "pass" if status == "pass" else "fail",
            pass_message if status == "pass" else f"Evidence marker for {check_id} did not pass.",
            details={"evidence": marker},
        )
    if not run_if_missing:
        return _check(check_id, "fail", f"Missing required evidence marker for {check_id}.")
    return runner()


def _evidence_marker(evidence_dir: Path | None, check_id: str) -> dict[str, Any] | None:
    if evidence_dir is None:
        return None
    for name in (f"{check_id}.json", check_id.replace("_passes", "") + ".json"):
        path = evidence_dir / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
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


def _check_version() -> dict[str, Any]:
    passed = __version__ == "2.6.0"
    return _check(
        "version_2_6_0",
        "pass" if passed else "fail",
        "Version is 2.6.0." if passed else f"Version is {__version__}.",
    )


def _check_contracts() -> dict[str, Any]:
    report = validate_v2_release_contracts()
    return _check(
        "v2_contracts_valid",
        "pass" if report["valid"] else "fail",
        "V2 contracts are valid." if report["valid"] else "V2 contracts are invalid.",
        details=report,
    )


def _check_api_v2(root: Path) -> dict[str, Any]:
    schema = create_app(root_dir=root).openapi()
    paths = set(schema.get("paths", {}))
    required = {"/api/v2/version", "/api/v2/projects", "/api/v2/admin/health"}
    missing = sorted(required - paths)
    return _check(
        "api_v2_exported",
        "pass" if not missing else "fail",
        "/api/v2 is exported." if not missing else "Missing /api/v2 routes.",
        details={"missing": missing},
    )


def _enterprise_golden_check(root: Path, work_dir: Path) -> dict[str, Any]:
    output = work_dir / "enterprise_golden"
    report = run_enterprise_golden_workflow(output_dir=output, root_dir=root)
    return _check(
        "enterprise_golden_workflow_passes",
        "pass" if report["status"] == "pass" else "fail",
        "Enterprise golden workflow passes."
        if report["status"] == "pass"
        else "Enterprise golden workflow failed.",
        details={"report_path": str(output / "enterprise_golden_report.json")},
    )


def _guardrail_check(work_dir: Path) -> dict[str, Any]:
    guardrail_dir = work_dir / "guardrail_benchmark"
    guardrail_dir.mkdir(parents=True, exist_ok=True)
    (guardrail_dir / "safe_report.md").write_text(
        "# Safe Report\n\nInternal research software validation artifact. "
        "No claims or procedural content.\n",
        encoding="utf-8",
    )
    report = run_guardrail_audit(guardrail_dir)
    return _check(
        "guardrail_benchmark_passes",
        "pass" if report.status == "pass" else "fail",
        "Guardrail benchmark passes." if report.status == "pass" else "Guardrail benchmark failed.",
        details=report.as_dict(),
    )


def _redteam_check(root: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests_redteam", "-q"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return _check(
        "red_team_suite_passes",
        "pass" if result.returncode == 0 else "fail",
        "Red-team suite passes." if result.returncode == 0 else "Red-team suite failed.",
        details={
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        },
    )


def _security_audit_check(work_dir: Path) -> dict[str, Any]:
    root = work_dir / "security_audit"
    root.mkdir(parents=True, exist_ok=True)
    report = run_security_audit(root_dir=root)
    return _check(
        "security_audit_passes",
        "pass" if report.status == "pass" else "fail",
        "Security audit passes." if report.status == "pass" else "Security audit failed.",
        details=report.as_dict(),
    )


def _isolation_check(work_dir: Path) -> dict[str, Any]:
    database = _seed_release_gate_database(work_dir / "isolation")
    report = run_isolation_audit(database)
    return _check(
        "isolation_audit_passes",
        "pass" if report["status"] == "pass" else "fail",
        "Isolation audit passes." if report["status"] == "pass" else "Isolation audit failed.",
        details=report,
    )


def _readiness_check(root: Path, work_dir: Path) -> dict[str, Any]:
    scratch = work_dir / "readiness"
    report = run_pilot_readiness_audit(
        PilotReadinessConfig.synthetic_dev(
            root_dir=root,
            database_path=scratch / "platform.sqlite",
            artifact_storage_path=scratch / "artifacts",
            backup_path=scratch / "backups",
        )
    )
    passed = report.failed_count == 0
    return _check(
        "readiness_audit_passes",
        "pass" if passed else "fail",
        "Readiness audit passes." if passed else "Readiness audit failed.",
        details=report.model_dump(mode="json"),
    )


def _dr_check(work_dir: Path) -> dict[str, Any]:
    database = _seed_release_gate_database(work_dir / "dr_source")
    report = run_disaster_recovery_drill(
        database,
        output_dir=work_dir / "dr_report",
        key_project_ids=["project-release-gate"],
        key_artifact_ids=["artifact-release-gate"],
    )
    return _check(
        "dr_drill_passes",
        "pass" if report.status == "pass" else "fail",
        "Disaster recovery drill passes." if report.status == "pass" else "DR drill failed.",
        details=report.to_dict(),
    )


def _backup_check(work_dir: Path) -> dict[str, Any]:
    database = _seed_release_gate_database(work_dir / "backup_source")
    backup = create_platform_backup(database, output_path=work_dir / "backup.zip")
    verification = verify_platform_backup(backup.path)
    return _check(
        "backup_verification_passes",
        "pass" if verification.status == "pass" else "fail",
        "Backup verification passes."
        if verification.status == "pass"
        else "Backup verification failed.",
        details=verification.to_dict(),
    )


def _support_bundle_check(work_dir: Path) -> dict[str, Any]:
    root = work_dir / "support_bundle"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "release.log").write_text(
        "service_token=release-gate-secret-token\n",
        encoding="utf-8",
    )
    bundle = create_support_bundle(root_dir=root, output_path=work_dir / "support_bundle.zip")
    archive_text = bundle.output_path.read_bytes().decode("latin1", errors="ignore")
    passed = "release-gate-secret-token" not in archive_text
    return _check(
        "support_bundle_redaction_passes",
        "pass" if passed else "fail",
        "Support bundle redaction passes."
        if passed
        else "Support bundle contains unredacted secret material.",
        details=bundle.manifest,
    )


def _slo_check(work_dir: Path) -> dict[str, Any]:
    database = _seed_release_gate_database(work_dir / "slo_source")
    backup_dir = work_dir / "slo_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "slo-backup.zip").write_text("synthetic backup marker\n", encoding="utf-8")
    report = generate_slo_report(database=database, backup_path=backup_dir)
    output = write_slo_report(report, work_dir / "slo_report.json")
    passed = output.exists() and report.status == "pass"
    return _check(
        "slo_report_generated",
        "pass" if passed else "fail",
        "SLO report generated." if passed else "SLO report was not healthy.",
        details={"status": report.status, "output": str(output)},
    )


def _deployment_check(root: Path) -> dict[str, Any]:
    missing = [path for path in REQUIRED_DEPLOYMENT_PATHS if not (root / path).exists()]
    deployment_text = (
        "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (root / "deployment").rglob("*")
            if path.is_file() and path.suffix.lower() in {".yml", ".yaml", ".md", ".sh", ".tpl"}
        )
        if (root / "deployment").exists()
        else ""
    )
    leaked = SECRET_ASSIGNMENT_RE.search(deployment_text) is not None
    passed = not missing and not leaked
    return _check(
        "deployment_smoke_passes",
        "pass" if passed else "fail",
        "Deployment smoke passes." if passed else "Deployment packaging smoke failed.",
        details={"missing": missing, "plaintext_secret_match": leaked},
    )


def _required_paths_check(
    check_id: str,
    message: str,
    root: Path,
    paths: tuple[str, ...],
) -> dict[str, Any]:
    missing = [path for path in paths if not (root / path).exists()]
    return _check(
        check_id,
        "pass" if not missing else "fail",
        message if not missing else f"Missing required paths for {check_id}.",
        details={"missing": missing},
    )


def _no_critical_todos_check(root: Path) -> dict[str, Any]:
    matches = _scan_text_files(root, CRITICAL_TODO_RE, production_only=False)
    return _check(
        "no_critical_todos",
        "pass" if not matches else "fail",
        "No critical TODOs found." if not matches else "Critical TODOs found.",
        details={"matches": matches[:25], "match_count": len(matches)},
    )


def _no_fixture_biomedical_data_check(root: Path) -> dict[str, Any]:
    fixture_roots = (
        [
            path
            for path in (root / "molecule_ranker").rglob("*")
            if path.is_dir() and path.name.lower() in {"fixtures", "fixture_data"}
        ]
        if (root / "molecule_ranker").exists()
        else []
    )
    matches: list[str] = []
    for fixture_root in fixture_roots:
        matches.extend(_scan_text_files(fixture_root, BIOMEDICAL_FIXTURE_RE, production_only=False))
    return _check(
        "no_fixture_biomedical_data_in_production",
        "pass" if not matches else "fail",
        "No fixture biomedical data in production package."
        if not matches
        else "Fixture biomedical data found in production package.",
        details={"matches": matches[:25], "match_count": len(matches)},
    )


def _no_plaintext_secrets_check(root: Path) -> dict[str, Any]:
    scan_roots = [
        path
        for path in (
            root / "deployment",
            root / "docs" / "v2",
            root / "docs" / "training",
            root / ".github",
        )
        if path.exists()
    ]
    matches: list[str] = []
    for scan_root in scan_roots:
        matches.extend(
            f"{scan_root.relative_to(root).as_posix()}/{match}"
            for match in _scan_text_files(scan_root, SECRET_ASSIGNMENT_RE, production_only=True)
        )
    return _check(
        "no_plaintext_secrets",
        "pass" if not matches else "fail",
        "No plaintext secrets found." if not matches else "Plaintext secrets found.",
        details={"matches": matches[:25], "match_count": len(matches)},
    )


def _scan_text_files(root: Path, pattern: re.Pattern[str], *, production_only: bool) -> list[str]:
    if not root.exists():
        return []
    ignored_parts = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".molecule-ranker",
    }
    if production_only:
        ignored_parts |= {"tests", "tests_validation", "tests_integration", "tests_redteam"}
    suffixes = {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".sh", ".txt"}
    matches: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            matches.append(path.relative_to(root).as_posix())
    return sorted(matches)


def _seed_release_gate_database(root: Path) -> PlatformDatabase:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    database = PlatformDatabase(root, db_path=root / "platform.sqlite")
    admin = database.create_user(
        email=f"release-gate-{uuid.uuid4().hex[:8]}@example.test",
        password="Release-gate-password-1",
        roles=["platform_admin", "user"],
    )
    now = datetime.now(UTC)
    artifact_path = root / "artifacts" / "release-gate-report.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "# Release Gate Report\nSoftware validation artifact.\n", encoding="utf-8"
    )
    with database.engine.begin() as connection:
        connection.execute(
            insert(project_workspaces).values(
                project_id="project-release-gate",
                org_id="default",
                name="Release Gate Project",
                root_dir=str(root),
                created_at=now,
                updated_at=now,
                metadata_json={},
            )
        )
        connection.execute(
            insert(artifact_records).values(
                artifact_id="artifact-release-gate",
                org_id="default",
                project_id="project-release-gate",
                run_id="run-release-gate",
                artifact_type="report",
                path=str(artifact_path),
                sha256="placeholder",
                size_bytes=artifact_path.stat().st_size,
                provenance_json={"source": "v2_release_gate"},
                created_at=now,
                metadata_json={},
            )
        )
    database.grant_project_permission(
        project_id="project-release-gate",
        role="project_owner",
        actor_user_id=admin.user_id,
        user_id=admin.user_id,
    )
    return database


def run_v2_release_gate_in_temp(root: Path) -> dict[str, Any]:
    with TemporaryDirectory(prefix="molecule-ranker-v2-release-gate-") as tmp:
        return run_v2_release_gate(V2ReleaseGateConfig(root_dir=root, output_dir=Path(tmp)))
