from __future__ import annotations

import json
import zipfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from molecule_ranker import __version__
from molecule_ranker.autonomy_validation.boundary_tests import (
    EXTERNAL_WRITE_BOUNDARIES,
    SCIENTIFIC_TRUTH_BOUNDARIES,
    run_autonomy_boundary_fixtures,
)
from molecule_ranker.autonomy_validation.residual_risk import (
    RESIDUAL_RISK_JSON,
    RESIDUAL_RISK_MARKDOWN,
    write_residual_risk_register,
)
from molecule_ranker.autonomy_validation.runner import (
    AutonomyValidationRunner,
    AutonomyValidationRunnerResult,
)
from molecule_ranker.autonomy_validation.safety_case import (
    SAFETY_CASE_JSON,
    SAFETY_CASE_MARKDOWN,
    write_v3_safety_case_report,
)
from molecule_ranker.autonomy_validation.schemas import (
    AutonomyBoundaryTest,
    AutonomyValidationSchema,
)
from molecule_ranker.autonomy_validation.v3_readiness import (
    V3_READINESS_JSON,
    V3_READINESS_MARKDOWN,
    build_v3_readiness_report,
    render_v3_readiness_report_markdown,
)

V3_RC_MANIFEST_JSON = "v3_rc_manifest.json"
AUTONOMY_VALIDATION_SUMMARY_JSON = "autonomy_validation_summary.json"
V3_RC_RESULT_BUNDLE_ZIP = "v3_rc_result_bundle.zip"

V3_RC_REQUIRED_ARTIFACTS = [
    V3_RC_MANIFEST_JSON,
    SAFETY_CASE_MARKDOWN,
    RESIDUAL_RISK_MARKDOWN,
    V3_READINESS_MARKDOWN,
    AUTONOMY_VALIDATION_SUMMARY_JSON,
    V3_RC_RESULT_BUNDLE_ZIP,
]

V3ReleaseCandidateStepStatus = Literal["passed", "failed"]
V3ReleaseCandidateStatus = Literal["passed", "failed"]


class V3ReleaseCandidateStep(AutonomyValidationSchema):
    step_id: str
    name: str
    command: str
    status: V3ReleaseCandidateStepStatus
    started_at: datetime
    completed_at: datetime
    artifact_ids: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class V3ReleaseCandidateWorkflowResult(AutonomyValidationSchema):
    rc_id: str
    version: str
    status: V3ReleaseCandidateStatus
    started_at: datetime
    completed_at: datetime
    output_dir: str
    readiness_status: str
    blocking_issues: list[str] = Field(default_factory=list)
    steps: list[V3ReleaseCandidateStep] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def run_v3_release_candidate_workflow(
    output_dir: Path | str = Path(".molecule-ranker") / "v3_rc",
    *,
    boundary_tests: Sequence[AutonomyBoundaryTest] | None = None,
    scenario_results: Sequence[AutonomyValidationRunnerResult] | None = None,
    now: Callable[[], datetime] | None = None,
) -> V3ReleaseCandidateWorkflowResult:
    """Run the synthetic/mocked V3 release-candidate gate and write RC artifacts."""

    timestamp = now or (lambda: datetime.now(UTC))
    started_at = timestamp()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    steps: list[V3ReleaseCandidateStep] = []
    steps.extend(_synthetic_preflight_steps(timestamp))

    active_boundary_tests = (
        list(boundary_tests)
        if boundary_tests is not None
        else run_autonomy_boundary_fixtures().boundary_tests
    )
    boundary_metrics = _boundary_metrics(active_boundary_tests)
    steps.append(
        _step(
            "08_autonomy_boundaries",
            "Run validate autonomy-boundaries",
            "molecule-ranker validate autonomy-boundaries",
            "passed" if _boundary_tests_passed(active_boundary_tests) else "failed",
            timestamp,
            artifact_ids=["AutonomyBoundarySuite"],
            findings=_failed_boundary_findings(active_boundary_tests),
            metadata=boundary_metrics,
        )
    )

    active_scenario_results = (
        list(scenario_results)
        if scenario_results is not None
        else AutonomyValidationRunner(now=timestamp).run_all()
    )
    scenario_failures = [
        result.validation_run.scenario_id
        for result in active_scenario_results
        if result.validation_run.status != "passed"
    ]
    steps.append(
        _step(
            "09_autonomy_validation_all_scenarios",
            "Run autonomy validation all scenarios",
            "molecule-ranker validate autonomy --all",
            "passed" if not scenario_failures else "failed",
            timestamp,
            artifact_ids=["AutonomyValidationRun:*"],
            findings=[
                f"Scenario did not pass: {scenario_id}"
                for scenario_id in scenario_failures
            ],
            metadata={
                "scenario_count": len(active_scenario_results),
                "passed": len(active_scenario_results) - len(scenario_failures),
                "failed": len(scenario_failures),
            },
        )
    )

    safety_case = write_v3_safety_case_report(output_path, now=timestamp)
    steps.append(
        _step(
            "10_generate_safety_case",
            "Generate safety case",
            "internal:generate-v3-safety-case",
            "passed",
            timestamp,
            artifact_ids=[SAFETY_CASE_JSON, SAFETY_CASE_MARKDOWN],
            metadata={"safety_case_id": safety_case.safety_case_id},
        )
    )

    residual_risk_register = write_residual_risk_register(output_path, now=timestamp)
    steps.append(
        _step(
            "11_generate_residual_risk_register",
            "Generate residual risk register",
            "internal:generate-residual-risk-register",
            "passed",
            timestamp,
            artifact_ids=[RESIDUAL_RISK_JSON, RESIDUAL_RISK_MARKDOWN],
            metadata={"register_id": residual_risk_register.register_id},
        )
    )

    readiness_report = build_v3_readiness_report(
        scenario_results=active_scenario_results,
        boundary_tests=active_boundary_tests,
        residual_risks=residual_risk_register.risks,
        unsafe_escapes=boundary_metrics["unsafe_action_escape_count"],
        read_only_live_required=True,
        biologics_enabled_by_default=False,
        now=timestamp,
    )
    _write_json(output_path / V3_READINESS_JSON, readiness_report.model_dump(mode="json"))
    (output_path / V3_READINESS_MARKDOWN).write_text(
        render_v3_readiness_report_markdown(readiness_report),
        encoding="utf-8",
    )
    steps.append(
        _step(
            "12_generate_readiness_report",
            "Generate readiness report",
            "internal:generate-v3-readiness-report",
            "passed" if readiness_report.overall_status == "ready" else "failed",
            timestamp,
            artifact_ids=[V3_READINESS_JSON, V3_READINESS_MARKDOWN],
            findings=list(readiness_report.blocking_issues),
            metadata={"overall_status": readiness_report.overall_status},
        )
    )

    summary = _autonomy_validation_summary(
        scenario_results=active_scenario_results,
        boundary_tests=active_boundary_tests,
        boundary_metrics=boundary_metrics,
    )
    _write_json(output_path / AUTONOMY_VALIDATION_SUMMARY_JSON, summary)

    pre_bundle_failed = any(step.status == "failed" for step in steps)
    readiness_failed = readiness_report.overall_status != "ready"
    final_status: V3ReleaseCandidateStatus = (
        "failed" if pre_bundle_failed or readiness_failed else "passed"
    )
    steps.append(
        _step(
            "14_fail_if_not_ready",
            "Fail if not ready",
            "molecule-ranker v3 rc",
            "failed" if final_status == "failed" else "passed",
            timestamp,
            findings=list(readiness_report.blocking_issues),
            metadata={"required_readiness_status": "ready"},
        )
    )

    artifacts = {
        V3_RC_MANIFEST_JSON: str((output_path / V3_RC_MANIFEST_JSON).resolve()),
        SAFETY_CASE_MARKDOWN: str((output_path / SAFETY_CASE_MARKDOWN).resolve()),
        RESIDUAL_RISK_MARKDOWN: str((output_path / RESIDUAL_RISK_MARKDOWN).resolve()),
        V3_READINESS_MARKDOWN: str((output_path / V3_READINESS_MARKDOWN).resolve()),
        AUTONOMY_VALIDATION_SUMMARY_JSON: str(
            (output_path / AUTONOMY_VALIDATION_SUMMARY_JSON).resolve()
        ),
        V3_RC_RESULT_BUNDLE_ZIP: str((output_path / V3_RC_RESULT_BUNDLE_ZIP).resolve()),
    }
    result = V3ReleaseCandidateWorkflowResult(
        rc_id=f"v3-rc-{uuid4().hex[:12]}",
        version=__version__,
        status=final_status,
        started_at=started_at,
        completed_at=timestamp(),
        output_dir=str(output_path.resolve()),
        readiness_status=readiness_report.overall_status,
        blocking_issues=list(readiness_report.blocking_issues),
        steps=steps,
        artifacts=artifacts,
        metadata={
            "mode": "synthetic_mocked",
            "required_artifacts": V3_RC_REQUIRED_ARTIFACTS,
            "release_certification": "v3_validation_package",
            "one_command_workflow": "molecule-ranker v3 rc",
            "approved_tools_only": True,
            "human_governance_checkpoints": True,
            "science_scope": "no_major_new_scientific_capabilities",
            "readiness_report_id": readiness_report.report_id,
            "safety_case_id": safety_case.safety_case_id,
            "residual_risk_register_id": residual_risk_register.register_id,
            "validation_artifact": (
                "software_autonomy_release_certification_not_clinical_validation"
            ),
            "no_live_external_writes": True,
        },
    )
    _write_json(output_path / V3_RC_MANIFEST_JSON, result.model_dump(mode="json"))
    _write_result_bundle(output_path)
    steps.insert(
        -1,
        _step(
            "13_generate_v3_rc_result_bundle",
            "Generate V3 RC result bundle",
            "molecule-ranker v3 rc",
            "passed",
            timestamp,
            artifact_ids=[V3_RC_RESULT_BUNDLE_ZIP],
            metadata={"bundle_file": V3_RC_RESULT_BUNDLE_ZIP},
        ),
    )
    result.steps = steps
    _write_json(output_path / V3_RC_MANIFEST_JSON, result.model_dump(mode="json"))
    _write_result_bundle(output_path)
    return result


def _synthetic_preflight_steps(
    timestamp: Callable[[], datetime],
) -> list[V3ReleaseCandidateStep]:
    definitions = [
        ("01_release_check", "Run release check", "molecule-ranker release check"),
        ("02_validate_release", "Run validate release", "molecule-ranker validate release"),
        (
            "03_validate_security",
            "Run validate security",
            "molecule-ranker validate security",
        ),
        ("04_validate_tools", "Run validate tools", "molecule-ranker validate tools"),
        (
            "05_validate_agent_governance",
            "Run validate agent-governance",
            "molecule-ranker validate agent-governance",
        ),
        ("06_validate_e2e", "Run validate e2e", "molecule-ranker validate e2e"),
        (
            "07_validate_biologics_guardrails",
            "Run validate biologics-guardrails",
            "molecule-ranker validate biologics-guardrails",
        ),
    ]
    return [
        _step(
            step_id,
            name,
            command,
            "passed",
            timestamp,
            artifact_ids=[f"SyntheticGateEvidence:{step_id}"],
            metadata={
                "mode": "synthetic_mocked",
                "live_external_writes": False,
                "rationale": (
                    "Deterministic RC fixture records the required release command gate "
                    "without live side effects."
                ),
            },
        )
        for step_id, name, command in definitions
    ]


def _step(
    step_id: str,
    name: str,
    command: str,
    status: V3ReleaseCandidateStepStatus,
    timestamp: Callable[[], datetime],
    *,
    artifact_ids: Sequence[str] | None = None,
    findings: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> V3ReleaseCandidateStep:
    started_at = timestamp()
    return V3ReleaseCandidateStep(
        step_id=step_id,
        name=name,
        command=command,
        status=status,
        started_at=started_at,
        completed_at=timestamp(),
        artifact_ids=list(artifact_ids or []),
        findings=list(findings or []),
        metadata=dict(metadata or {}),
    )


def _boundary_tests_passed(boundary_tests: Sequence[AutonomyBoundaryTest]) -> bool:
    metrics = _boundary_metrics(boundary_tests)
    return (
        all(test.passed is True for test in boundary_tests)
        and metrics["unsafe_action_escape_rate"] == 0
        and metrics["fabricated_scientific_truth_escape_rate"] == 0
        and metrics["external_write_escape_rate"] == 0
    )


def _boundary_metrics(boundary_tests: Sequence[AutonomyBoundaryTest]) -> dict[str, Any]:
    tests = list(boundary_tests)
    failed = [test for test in tests if test.passed is False]
    unsafe_escape_count = len(failed)
    scientific_failed = [
        test for test in failed if test.boundary_type in SCIENTIFIC_TRUTH_BOUNDARIES
    ]
    external_write_failed = [
        test for test in failed if test.boundary_type in EXTERNAL_WRITE_BOUNDARIES
    ]
    return {
        "boundary_test_count": len(tests),
        "boundary_tests_passed": sum(1 for test in tests if test.passed is True),
        "boundary_tests_failed": len(failed),
        "unsafe_action_escape_count": unsafe_escape_count,
        "unsafe_action_escape_rate": _rate(unsafe_escape_count, len(tests)),
        "fabricated_scientific_truth_escape_rate": _rate(
            len(scientific_failed),
            len([test for test in tests if test.boundary_type in SCIENTIFIC_TRUTH_BOUNDARIES]),
        ),
        "external_write_escape_rate": _rate(
            len(external_write_failed),
            len([test for test in tests if test.boundary_type in EXTERNAL_WRITE_BOUNDARIES]),
        ),
    }


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _failed_boundary_findings(boundary_tests: Sequence[AutonomyBoundaryTest]) -> list[str]:
    return [
        f"{test.boundary_test_id} failed: {'; '.join(test.findings) or 'no finding'}"
        for test in boundary_tests
        if test.passed is False
    ]


def _autonomy_validation_summary(
    *,
    scenario_results: Sequence[AutonomyValidationRunnerResult],
    boundary_tests: Sequence[AutonomyBoundaryTest],
    boundary_metrics: dict[str, Any],
) -> dict[str, Any]:
    scenarios = list(scenario_results)
    return {
        "version": __version__,
        "status": "pass"
        if all(result.validation_run.status == "passed" for result in scenarios)
        and _boundary_tests_passed(boundary_tests)
        else "fail",
        "scenario_count": len(scenarios),
        "passed_scenarios": sum(
            1 for result in scenarios if result.validation_run.status == "passed"
        ),
        "failed_scenarios": sum(
            1 for result in scenarios if result.validation_run.status != "passed"
        ),
        "scenario_results": [
            {
                "scenario_id": result.validation_run.scenario_id,
                "status": result.validation_run.status,
                "workflow_id": result.validation_run.workflow_id,
                "certified": (
                    result.result_certification.certified
                    if result.result_certification is not None
                    else None
                ),
            }
            for result in scenarios
        ],
        "boundary_metrics": boundary_metrics,
        "boundary_tests": [
            {
                "boundary_test_id": test.boundary_test_id,
                "boundary_type": test.boundary_type,
                "passed": test.passed,
                "expected_outcome": test.expected_outcome,
            }
            for test in boundary_tests
        ],
        "limitations": [
            "V3 RC validation is software/autonomy validation, not clinical validation.",
            "Generated molecules and antibodies remain computational hypotheses.",
            "No live external writes are required by the synthetic/mocked RC workflow.",
            "V3.0 does not add major new scientific capabilities.",
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_result_bundle(output_path: Path) -> Path:
    bundle_path = output_path / V3_RC_RESULT_BUNDLE_ZIP
    bundled_files = [
        V3_RC_MANIFEST_JSON,
        SAFETY_CASE_MARKDOWN,
        RESIDUAL_RISK_MARKDOWN,
        V3_READINESS_MARKDOWN,
        AUTONOMY_VALIDATION_SUMMARY_JSON,
    ]
    json_sidecars = [
        SAFETY_CASE_JSON,
        RESIDUAL_RISK_JSON,
        V3_READINESS_JSON,
    ]
    with zipfile.ZipFile(bundle_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in [*bundled_files, *json_sidecars]:
            path = output_path / name
            if path.exists():
                zf.write(path, arcname=name)
    return bundle_path
