from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker import __version__
from molecule_ranker.autonomy_validation.boundary_tests import (
    run_autonomy_boundary_fixtures,
)
from molecule_ranker.autonomy_validation.residual_risk import (
    RESIDUAL_RISK_JSON,
    RESIDUAL_RISK_MARKDOWN,
    write_residual_risk_register,
)
from molecule_ranker.autonomy_validation.safety_case import (
    SAFETY_CASE_JSON,
    SAFETY_CASE_MARKDOWN,
    write_v3_safety_case_report,
)
from molecule_ranker.autonomy_validation.v3_readiness import build_v3_readiness_report
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.v3.certification import V3ResultCertification
from molecule_ranker.v3.discover import V3DiscoverRequest, run_v3_discover
from molecule_ranker.v3.product_contract import get_v3_product_contract
from molecule_ranker.v3.result_bundle import V3ResultBundle
from molecule_ranker.v3.validation import run_v3_validation

V3_RELEASE_GATE_VERSION = "v3.release-gate.1"
V3_RELEASE_GATE_JSON = "v3_release_gate.json"
V3_RELEASE_GATE_MARKDOWN = "v3_release_gate.md"
V3ReleaseGateStatus = Literal["pass", "fail"]

EXPENSIVE_V3_RELEASE_EVIDENCE_CHECKS = {
    "tests_pass_marker",
    "ruff_pass_marker",
    "pyright_pass_marker",
    "docker_build_pass_marker",
    "v3_validation_passes",
}
REQUIRED_DEMO_NAMES = (
    "mocked_full_discovery_loop",
    "dry_run_full_discovery_loop",
    "read_only_live_small_molecule_loop",
    "mocked_biologics_loop",
    "campaign_copilot_event_loop",
    "integration_dry_run_loop",
    "result_certification_demo",
    "boundary_test_demo",
)
REQUIRED_DEPLOYMENT_DOCS = (
    "deployment/README.md",
    "docs/runbooks/deployment.md",
    "docs/runbooks/production_config.md",
)
REQUIRED_TRAINING_DOCS = (
    "docs/training/admin_training.md",
    "docs/training/scientist_training.md",
    "docs/training/reviewer_training.md",
    "docs/training/operator_training.md",
    "docs/training/codex_guardrails_training.md",
)


@dataclass(frozen=True)
class V3ReleaseGateConfig:
    root_dir: Path = Path(".")
    output_dir: Path = Path(".")
    evidence_dir: Path | None = None
    run_expensive_checks: bool = True


def run_v3_release_gate(config: V3ReleaseGateConfig | None = None) -> dict[str, Any]:
    active = config or V3ReleaseGateConfig()
    root = active.root_dir.resolve()
    output_dir = active.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / ".molecule-ranker" / "v3-release-gate"
    work_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = active.evidence_dir.resolve() if active.evidence_dir else None

    discover_result = run_v3_discover(
        V3DiscoverRequest(
            disease="Synthetic V3 release gate condition",
            mode="mocked",
            output_dir=work_dir / "mocked_discover",
        )
    )
    checks = [
        _check_version(),
        _package_lock_check(root),
        _evidence_or_run(
            "tests_pass_marker",
            "Tests pass marker is present.",
            evidence_dir,
            lambda: _missing_expensive_marker("tests_pass_marker"),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "ruff_pass_marker",
            "Ruff pass marker is present.",
            evidence_dir,
            lambda: _missing_expensive_marker("ruff_pass_marker"),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "pyright_pass_marker",
            "Pyright pass marker is present.",
            evidence_dir,
            lambda: _missing_expensive_marker("pyright_pass_marker"),
            active.run_expensive_checks,
        ),
        _evidence_or_run(
            "docker_build_pass_marker",
            "Docker build pass marker is present.",
            evidence_dir,
            lambda: _missing_expensive_marker("docker_build_pass_marker"),
            active.run_expensive_checks,
        ),
        _product_contract_check(),
        _result_bundle_contract_check(discover_result.artifacts),
        _mocked_discover_check(discover_result),
        _evidence_or_run(
            "v3_validation_passes",
            "V3 validation passes.",
            evidence_dir,
            lambda: _v3_validation_check(work_dir / "v3_validation"),
            active.run_expensive_checks,
        ),
        _readiness_check(),
        _safety_case_check(work_dir / "safety_case"),
        _residual_risk_check(work_dir / "residual_risks"),
        _support_bundle_redaction_check(),
        _required_paths_check("deployment_docs_exist", root, REQUIRED_DEPLOYMENT_DOCS),
        _required_paths_check("training_docs_exist", root, REQUIRED_TRAINING_DOCS),
        _result_certification_check(discover_result.artifacts),
        _required_demos_check(root),
        _autonomy_boundary_check(),
    ]
    status: V3ReleaseGateStatus = (
        "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    )
    report = {
        "gate_id": f"v3-release-gate-{uuid.uuid4().hex[:16]}",
        "gate_version": V3_RELEASE_GATE_VERSION,
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
            "software_autonomy_release_gate": True,
            "not_clinical_validation": True,
            "no_medical_lab_synthesis_or_dosing_guidance": True,
        },
    }
    write_v3_release_gate_outputs(report, output_dir=output_dir)
    return report


def write_v3_release_gate_outputs(report: dict[str, Any], *, output_dir: Path) -> dict[str, Path]:
    json_path = output_dir / V3_RELEASE_GATE_JSON
    markdown_path = output_dir / V3_RELEASE_GATE_MARKDOWN
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_v3_release_gate_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def render_v3_release_gate_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V3 Release Gate",
        "",
        f"- Status: `{report['status']}`",
        f"- Version: `{report['version']}`",
        f"- Gate ID: `{report['gate_id']}`",
        f"- Passed checks: {report['summary']['pass']}",
        f"- Failed checks: {report['summary']['fail']}",
        "",
        "This is software/autonomy release evidence, not clinical validation.",
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
            pass_message if status == "pass" else f"Evidence marker for {check_id} failed.",
            details={"evidence": marker},
        )
    if not run_if_missing:
        return _check(check_id, "fail", f"Missing required evidence marker for {check_id}.")
    return runner()


def _evidence_marker(evidence_dir: Path | None, check_id: str) -> dict[str, Any] | None:
    if evidence_dir is None:
        return None
    path = evidence_dir / f"{check_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _check(
    check_id: str,
    status: V3ReleaseGateStatus,
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
    passed = __version__ == "3.0.0"
    return _check(
        "version_is_3_0_0",
        "pass" if passed else "fail",
        "Version is 3.0.0." if passed else f"Version is {__version__}.",
    )


def _package_lock_check(root: Path) -> dict[str, Any]:
    existing = [
        path for path in ("uv.lock", "poetry.lock", "Pipfile.lock") if (root / path).exists()
    ]
    return _check(
        "package_lock_present",
        "pass" if existing else "fail",
        "Package lock is present." if existing else "No package lock found.",
        details={"locks": existing},
    )


def _missing_expensive_marker(check_id: str) -> dict[str, Any]:
    return _check(check_id, "fail", f"{check_id} requires an explicit evidence marker.")


def _product_contract_check() -> dict[str, Any]:
    try:
        contract = get_v3_product_contract()
    except ValueError as exc:
        return _check("v3_product_contract_valid", "fail", str(exc))
    return _check(
        "v3_product_contract_valid",
        "pass" if contract.product_version == "3.0.0" else "fail",
        "V3 product contract is valid.",
        details={"contract_version": contract.product_contract_version},
    )


def _result_bundle_contract_check(artifacts: dict[str, str]) -> dict[str, Any]:
    bundle_path = artifacts.get("v3_result_bundle.json")
    if not bundle_path:
        return _check("v3_result_bundle_contract_valid", "fail", "V3 result bundle missing.")
    try:
        bundle = V3ResultBundle.model_validate_json(Path(bundle_path).read_text(encoding="utf-8"))
    except ValueError as exc:
        return _check("v3_result_bundle_contract_valid", "fail", str(exc))
    return _check(
        "v3_result_bundle_contract_valid",
        "pass",
        "V3 result bundle contract is valid.",
        details={"bundle_id": bundle.bundle_id},
    )


def _mocked_discover_check(result: Any) -> dict[str, Any]:
    passed = result.status == "succeeded" and result.certification_passed
    return _check(
        "v3_mocked_discover_passes",
        "pass" if passed else "fail",
        "V3 mocked discover passes." if passed else "V3 mocked discover failed.",
        details={
            "status": result.status,
            "certification_passed": result.certification_passed,
            "output_dir": result.output_dir,
        },
    )


def _v3_validation_check(output_dir: Path) -> dict[str, Any]:
    report = run_v3_validation(output_dir=output_dir, mode="mocked")
    return _check(
        "v3_validation_passes",
        "pass" if report.status == "pass" else "fail",
        "V3 validation passes." if report.status == "pass" else "V3 validation failed.",
        details={"report_id": report.report_id, "hard_failures": report.hard_failures},
    )


def _readiness_check() -> dict[str, Any]:
    report = build_v3_readiness_report()
    allowed = report.overall_status in {"ready", "ready_with_warnings"}
    no_critical = not any("critical" in issue.lower() for issue in report.blocking_issues)
    return _check(
        "v3_readiness_ready",
        "pass" if allowed and no_critical else "fail",
        "V3 readiness is ready or ready_with_warnings with no critical blockers."
        if allowed and no_critical
        else "V3 readiness has critical blockers or is not ready.",
        details={
            "overall_status": report.overall_status,
            "blocking_issues": report.blocking_issues,
        },
    )


def _safety_case_check(output_dir: Path) -> dict[str, Any]:
    report = write_v3_safety_case_report(output_dir)
    json_path = output_dir / SAFETY_CASE_JSON
    md_path = output_dir / SAFETY_CASE_MARKDOWN
    passed = json_path.exists() and md_path.exists()
    return _check(
        "safety_case_report_exists",
        "pass" if passed else "fail",
        "Safety case report exists." if passed else "Safety case report is missing.",
        details={
            "safety_case_id": report.safety_case_id,
            "json": str(json_path),
            "markdown": str(md_path),
        },
    )


def _residual_risk_check(output_dir: Path) -> dict[str, Any]:
    register = write_residual_risk_register(output_dir)
    unmitigated = [
        risk.risk_id
        for risk in register.risks
        if risk.severity == "critical" and risk.status == "open" and not risk.mitigation.strip()
    ]
    json_path = output_dir / RESIDUAL_RISK_JSON
    md_path = output_dir / RESIDUAL_RISK_MARKDOWN
    passed = json_path.exists() and md_path.exists() and not unmitigated
    return _check(
        "residual_risk_register_clean",
        "pass" if passed else "fail",
        "Residual risk register exists with no open critical unmitigated risk."
        if passed
        else "Residual risk register has open critical unmitigated risk.",
        details={"unmitigated_critical_risks": unmitigated},
    )


def _support_bundle_redaction_check() -> dict[str, Any]:
    raw = "support transcript\napi_key=sk_release_gate_secret\ntoken: release-gate-token\n"
    redacted = redact_secrets(raw)
    leaked = "sk_release_gate_secret" in redacted or "release-gate-token" in redacted
    return _check(
        "support_bundle_redaction_passes",
        "pass" if not leaked else "fail",
        "Support bundle redaction passes." if not leaked else "Support bundle secret leaked.",
    )


def _required_paths_check(
    check_id: str,
    root: Path,
    required_paths: tuple[str, ...],
) -> dict[str, Any]:
    missing = [path for path in required_paths if not (root / path).exists()]
    return _check(
        check_id,
        "pass" if not missing else "fail",
        f"{check_id.replace('_', ' ')}." if not missing else "Required paths are missing.",
        details={"missing": missing},
    )


def _result_certification_check(artifacts: dict[str, str]) -> dict[str, Any]:
    path = artifacts.get("v3_result_certification.json")
    if not path:
        return _check("result_certification_passes", "fail", "Certification artifact missing.")
    certification = V3ResultCertification.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    return _check(
        "result_certification_passes",
        "pass" if certification.certified else "fail",
        "Result certification passes."
        if certification.certified
        else "Result certification failed.",
        details={"certification_id": certification.certification_id},
    )


def _required_demos_check(root: Path) -> dict[str, Any]:
    missing: list[str] = []
    for demo_name in REQUIRED_DEMO_NAMES:
        demo_dir = root / "examples" / "v3_0" / demo_name
        for filename in ("README.md", "run.sh", "expected_output_manifest.json"):
            if not (demo_dir / filename).exists():
                missing.append(f"examples/v3_0/{demo_name}/{filename}")
    return _check(
        "required_demos_present",
        "pass" if not missing else "fail",
        "All required V3 demos are present." if not missing else "Required V3 demos are missing.",
        details={"missing": missing},
    )


def _autonomy_boundary_check() -> dict[str, Any]:
    result = run_autonomy_boundary_fixtures()
    failed = [test.boundary_test_id for test in result.boundary_tests if test.passed is False]
    hard_failures: list[str] = []
    if result.unsafe_action_escape_rate > 0:
        hard_failures.append("unsafe_escape_rate > 0")
    if result.external_write_escape_rate > 0:
        hard_failures.append("external_write_escape_rate > 0")
    if result.fabricated_scientific_truth_escape_rate > 0:
        hard_failures.append("fabricated scientific truth escape")
    passed = result.passed and not failed and not hard_failures
    return _check(
        "autonomy_boundaries_pass",
        "pass" if passed else "fail",
        "Autonomy boundary tests pass." if passed else "Autonomy boundary test failed.",
        details={"failed": failed, "hard_failures": hard_failures},
    )


__all__ = [
    "EXPENSIVE_V3_RELEASE_EVIDENCE_CHECKS",
    "V3ReleaseGateConfig",
    "V3_RELEASE_GATE_JSON",
    "V3_RELEASE_GATE_MARKDOWN",
    "V3_RELEASE_GATE_VERSION",
    "render_v3_release_gate_markdown",
    "run_v3_release_gate",
    "write_v3_release_gate_outputs",
]
