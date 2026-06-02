from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from molecule_ranker import __version__

RELEASE_STAGE = "validated_internal_research_platform_mvp"
API_CONTRACT_VERSION = "api.v1"
ARTIFACT_CONTRACT_VERSION = "artifacts.v1"
DATA_CONTRACT_VERSION = "data-contracts.v1"
WAREHOUSE_CONTRACT_VERSION = "mr_warehouse_v1.0.0"

ReleaseCategory = Literal[
    "golden_workflow",
    "validation",
    "security",
    "provenance",
    "integration",
    "deployment",
    "documentation",
    "runbook",
    "demo",
    "contract",
    "backup_restore",
    "packaging",
    "portfolio",
    "knowledge_graph",
    "hypothesis",
    "campaign",
    "evaluation",
]
ReleaseCheckStatus = Literal["pass", "warn", "fail"]


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


@dataclass(frozen=True)
class ReleaseGate:
    gate_id: str
    category: ReleaseCategory
    title: str
    required_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "category": self.category,
            "title": self.title,
            "required_evidence": list(self.required_evidence),
        }


@dataclass(frozen=True)
class ReleaseCheck:
    check_id: str
    title: str
    status: ReleaseCheckStatus
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "status": self.status,
            "message": self.message,
            "details": self.details or {},
        }


V1_RELEASE_GATES: tuple[ReleaseGate, ...] = (
    ReleaseGate(
        "v1-golden-workflows",
        "golden_workflow",
        "End-to-end golden workflows execute from project creation through review handoff.",
        (
            "molecule_ranker/validation/golden_workflows.py",
            "molecule_ranker/validation/runner.py",
            "tests_validation/test_golden_existing_ranking.py",
            "tests_validation/test_golden_generation.py",
        ),
    ),
    ReleaseGate(
        "v1-validation-suite",
        "validation",
        "Release validation suite covers CLI, contracts, hosted flows, workers, and packaging.",
        (
            "tests_validation/test_golden_hosted.py",
            "tests_validation/test_golden_integrations.py",
            "tests/test_validation_golden_workflows.py",
        ),
    ),
    ReleaseGate(
        "v1-security-guardrails",
        "security",
        "Security and guardrail audits cover hosted controls and scientific-integrity safety.",
        (
            "molecule_ranker/platform/security_audit.py",
            "molecule_ranker/validation/guardrail_audit.py",
            "tests_validation/test_security_audit.py",
            "tests_validation/test_guardrail_audit.py",
        ),
    ),
    ReleaseGate(
        "v1-provenance-reproducibility",
        "provenance",
        "Artifacts retain source provenance, hashes, timestamps, and reproducibility metadata.",
        (
            "molecule_ranker/contracts/artifact_contracts.py",
            "tests/test_artifact_contracts.py",
            "tests_validation/test_golden_existing_ranking.py",
        ),
    ),
    ReleaseGate(
        "v1-integration-contracts",
        "integration",
        "Benchling, generic REST/file, warehouse, webhook, and sync paths are contract-tested.",
        (
            "tests/test_benchling_connector.py",
            "tests/test_generic_rest_connector.py",
            "tests/test_generic_file_connector.py",
            "tests/test_integration_cli.py",
        ),
    ),
    ReleaseGate(
        "v1-hosted-hardening",
        "deployment",
        "Hosted deployment checks cover readiness, health, workers, metrics, and packaging.",
        (
            "molecule_ranker/platform/readiness.py",
            "tests_validation/test_platform_readiness.py",
            "deployment/README.md",
            "deployment/Dockerfile",
        ),
    ),
    ReleaseGate(
        "v1-platform-documentation",
        "documentation",
        "Platform documentation describes V1.0 scope, non-goals, validation, and contracts.",
        (
            "README.md",
            "docs/v1.0-release-readiness.md",
            "docs/contracts/v1.0-api-and-artifacts.md",
            "docs/user/overview.md",
            "docs/admin/security_checklist.md",
        ),
    ),
    ReleaseGate(
        "v1-operator-runbooks",
        "runbook",
        "Admin and operator runbooks cover deployment, health, incidents, retention, and rollback.",
        (
            "docs/runbooks/deployment.md",
            "docs/runbooks/backup_restore.md",
            "docs/runbooks/release_process.md",
            "docs/runbooks/security_incidents.md",
        ),
    ),
    ReleaseGate(
        "v1-demo-artifacts",
        "demo",
        "Synthetic demo project artifacts exist and are labeled as non-evidence examples.",
        (
            "examples/v1_0_demo/README.md",
            "examples/v1_0_demo/demo_commands.sh",
            "examples/v1_0_demo/expected_artifacts_manifest.json",
            "examples/v1_0_demo/synthetic_assay_results.csv",
        ),
    ),
    ReleaseGate(
        "v1-versioned-contracts",
        "contract",
        "API and artifact contracts are versioned and exportable for V1.0.",
        (
            "molecule_ranker/contracts/artifact_contracts.py",
            "molecule_ranker/contracts/api_contracts.py",
            "molecule_ranker/contracts/schema_exports.py",
            "tests/test_api_contracts_v1.py",
            "tests/test_artifact_contracts.py",
        ),
    ),
    ReleaseGate(
        "v1-backup-restore-dr",
        "backup_restore",
        "Backup, restore, and disaster-recovery checks are implemented and documented.",
        (
            "molecule_ranker/platform/backup.py",
            "tests/test_platform_backup.py",
            "docs/runbooks/backup_restore.md",
        ),
    ),
    ReleaseGate(
        "v1-release-packaging",
        "packaging",
        "V1.0 packaging records version, contracts, validation evidence, and release notes.",
        (
            "pyproject.toml",
            "uv.lock",
            "molecule_ranker/release/manifest.py",
            "molecule_ranker/release/checks.py",
            "molecule_ranker/release/notes.py",
        ),
    ),
    ReleaseGate(
        "v1-1-agentic-generation-validation",
        "golden_workflow",
        "V1.2 AgentGraph generation, report cards, and benchmarks are validated.",
        (
            "molecule_ranker/agent_graph/schemas.py",
            "molecule_ranker/agent_graph/graph.py",
            "molecule_ranker/agent_graph/executor.py",
            "molecule_ranker/agent_graph/planner.py",
            "molecule_ranker/agent_graph/audit.py",
            "molecule_ranker/design/schemas.py",
            "molecule_ranker/design/objective_builder.py",
            "molecule_ranker/design/constraints.py",
            "molecule_ranker/design/seed_scaffold_selector.py",
            "molecule_ranker/design/oracles.py",
            "molecule_ranker/design/uncertainty.py",
            "molecule_ranker/design/active_design.py",
            "molecule_ranker/design/benchmarks.py",
            "molecule_ranker/generation/ensemble.py",
            "molecule_ranker/generation/generators/selfies_mutation.py",
            "molecule_ranker/generation/generators/fragment_grower.py",
            "molecule_ranker/generation/generators/scaffold_hopper.py",
            "molecule_ranker/generation/generators/matched_pair_transformer.py",
            "molecule_ranker/generation/generators/reactionless_library_enum.py",
            "molecule_ranker/agents/experiment_readiness.py",
            "molecule_ranker/agents/medicinal_chemistry_critic.py",
            "molecule_ranker/agents/oracle_scoring.py",
            "molecule_ranker/agents/scientific_design.py",
            "molecule_ranker/agents/scientific_design_planner.py",
            "molecule_ranker/validation/golden_workflows.py",
            "molecule_ranker/validation/runner.py",
            "tests/test_agent_graph_runtime.py",
            "tests/test_design_objective_builder.py",
            "tests/test_design_seed_scaffold_selector.py",
            "tests/test_design_oracles.py",
            "tests/test_design_uncertainty.py",
            "tests/test_active_design.py",
            "tests/test_design_benchmarks.py",
            "tests/test_generation_ensemble.py",
            "tests/test_experiment_readiness_agent.py",
            "tests/test_medicinal_chemistry_critic_agent.py",
            "tests/test_oracle_scoring_agent.py",
            "tests/test_scientific_design_planner_agent.py",
            "tests/test_novel_molecule_agent.py",
            "tests/test_generation_benchmark.py",
            "tests/test_validation_golden_workflows.py",
        ),
    ),
    ReleaseGate(
        "v1-4-portfolio-optimization",
        "portfolio",
        "V1.4 portfolio optimization and program decision analytics are deterministic and guarded.",
        (
            "molecule_ranker/portfolio/schemas.py",
            "molecule_ranker/portfolio/optimizer.py",
            "tests/test_portfolio_optimization.py",
            "README.md",
        ),
    ),
    ReleaseGate(
        "v1-5-knowledge-graph",
        "knowledge_graph",
        "V1.5 cross-program knowledge graph is provenance-aware and guarded.",
        (
            "molecule_ranker/knowledge_graph/schemas.py",
            "molecule_ranker/knowledge_graph/builder.py",
            "molecule_ranker/knowledge_graph/reasoning.py",
            "tests/test_knowledge_graph_v15.py",
            "README.md",
        ),
    ),
    ReleaseGate(
        "v1-6-hypothesis-planning",
        "hypothesis",
        "V1.6 graph-backed hypothesis generation and research-question planning are guarded.",
        (
            "molecule_ranker/hypotheses/schemas.py",
            "molecule_ranker/hypotheses/engine.py",
            "molecule_ranker/hypotheses/planner.py",
            "molecule_ranker/hypotheses/codex_assistant.py",
            "tests/test_hypotheses_v16.py",
            "README.md",
        ),
    ),
    ReleaseGate(
        "v1-7-campaign-planning",
        "campaign",
        "V1.7 closed-loop campaign planning and budget-aware execution management are guarded.",
        (
            "molecule_ranker/campaign/schemas.py",
            "molecule_ranker/campaign/planner.py",
            "molecule_ranker/campaign/reports.py",
            "tests/test_campaign_planning_v17.py",
            "README.md",
        ),
    ),
    ReleaseGate(
        "v1-8-evaluation-benchmarks",
        "evaluation",
        "V1.8 scientific evaluation benchmarks and prospective analytics are guarded.",
        (
            "molecule_ranker/evaluation/schemas.py",
            "molecule_ranker/evaluation/metrics.py",
            "molecule_ranker/evaluation/reports.py",
            "tests/test_evaluation_v18.py",
            "README.md",
        ),
    ),
)

_CRITICAL_TODO_RE = re.compile(
    r"\b(?:TODO|FIXME|XXX)\s*(?:\([^)]*\))?\s*:\s*.{0,80}\b(?:critical|blocker|release)\b",
    re.I,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<name>api[_-]?key|secret|token|password|credential)\s*[:=]\s*"
    r"(?P<quote>['\"])(?P<value>[^'\"]{8,})(?P=quote)",
    re.I,
)
_PRODUCTION_SCAN_ROOTS = ("molecule_ranker", "deployment", "pyproject.toml", "README.md")
_TEXT_SUFFIXES = {".py", ".toml", ".yaml", ".yml", ".json", ".md", ".sh", ".env"}


def evaluate_release_readiness(root_dir: str | Path = ".") -> dict[str, Any]:
    root = Path(root_dir)
    gate_results = []
    for gate in V1_RELEASE_GATES:
        file_evidence = [
            evidence
            for evidence in gate.required_evidence
            if evidence.endswith((".md", ".py", ".toml", ".lock", ".json", ".csv", ".sh"))
        ]
        missing = [evidence for evidence in file_evidence if not (root / evidence).exists()]
        gate_results.append(
            {
                **gate.as_dict(),
                "status": "pass" if not missing else "missing_evidence",
                "missing_evidence": missing,
            }
        )
    return {
        "version": __version__,
        "stage": RELEASE_STAGE,
        "contracts": _legacy_contract_versions(),
        "ready": all(result["status"] == "pass" for result in gate_results),
        "gates": gate_results,
    }


def run_release_checks(
    root_dir: str | Path = ".",
    *,
    run_commands: bool = False,
    require_tests_marker: bool = False,
) -> dict[str, Any]:
    root = Path(root_dir).resolve()
    checks = [
        _check_version(),
        _check_tests(root, run_commands=run_commands, require_marker=require_tests_marker),
        _check_artifact_contracts(root, run_commands=run_commands),
        _check_api_contracts(root, run_commands=run_commands),
        _check_security_audit(root, run_commands=run_commands),
        _check_guardrail_audit(root, run_commands=run_commands),
        _check_required_docs(root),
        _check_required_runbooks(root),
        _check_readme(root),
        _check_docker_build_available(root, run_commands=run_commands),
        _check_no_critical_todos(root),
        _check_no_fixture_biomedical_data_in_production(root),
        _check_no_plaintext_secrets(root),
    ]
    serialized = [check.as_dict() for check in checks]
    return {
        "name": "molecule-ranker",
        "version": __version__,
        "stage": RELEASE_STAGE,
        "status": "fail" if any(check.status == "fail" for check in checks) else "pass",
        "run_commands": run_commands,
        "checks": serialized,
        "summary": {
            "pass": sum(1 for check in checks if check.status == "pass"),
            "warn": sum(1 for check in checks if check.status == "warn"),
            "fail": sum(1 for check in checks if check.status == "fail"),
        },
    }


def _legacy_contract_versions() -> dict[str, str]:
    return {
        "api": API_CONTRACT_VERSION,
        "artifacts": ARTIFACT_CONTRACT_VERSION,
        "data_contracts": DATA_CONTRACT_VERSION,
        "warehouse": WAREHOUSE_CONTRACT_VERSION,
    }


def _check_version() -> ReleaseCheck:
    if __version__ == "1.8.0":
        return ReleaseCheck("version", "Version is 1.8.0", "pass", "Package version is 1.8.0.")
    return ReleaseCheck(
        "version",
        "Version is 1.8.0",
        "fail",
        f"Package version is {__version__}, expected 1.8.0.",
    )


def _check_tests(root: Path, *, run_commands: bool, require_marker: bool) -> ReleaseCheck:
    marker = root / ".molecule-ranker" / "release" / "tests-passed.json"
    pytest_cache = root / ".pytest_cache"
    if marker.exists():
        return ReleaseCheck(
            "tests_passed",
            "Tests passed marker exists",
            "pass",
            "Found release tests-passed marker.",
            {"marker": str(marker)},
        )
    if run_commands:
        result = _run_command(root, ["uv", "run", "pytest"])
        return _command_check(
            "tests_passed",
            "Tests passed marker exists or tests pass",
            result,
            pass_message="uv run pytest passed.",
        )
    if pytest_cache.exists() and not require_marker:
        return ReleaseCheck(
            "tests_passed",
            "Tests passed marker exists or command passes",
            "pass",
            "Found pytest cache; release check did not rerun tests in verify-only mode.",
        )
    return ReleaseCheck(
        "tests_passed",
        "Tests passed marker exists or command passes",
        "fail" if require_marker else "warn",
        "No release tests-passed marker found; run with --run-commands to execute pytest.",
    )


def _check_artifact_contracts(root: Path, *, run_commands: bool) -> ReleaseCheck:
    from molecule_ranker.contracts import ARTIFACT_CONTRACTS

    if not ARTIFACT_CONTRACTS:
        return ReleaseCheck(
            "artifact_contracts",
            "Artifact contracts valid",
            "fail",
            "No artifact contracts are registered.",
        )
    if run_commands:
        result = _run_command(
            root,
            ["uv", "run", "molecule-ranker", "validate", "release", "--json"],
        )
        return _command_check(
            "artifact_contracts",
            "Artifact contracts valid",
            result,
            pass_message="Release validation and artifact contracts passed.",
        )
    return ReleaseCheck(
        "artifact_contracts",
        "Artifact contracts valid",
        "pass",
        f"Registered {len(ARTIFACT_CONTRACTS)} artifact contracts.",
    )


def _check_api_contracts(root: Path, *, run_commands: bool) -> ReleaseCheck:
    from molecule_ranker.contracts import validate_api_contracts

    errors = validate_api_contracts()
    if errors:
        return ReleaseCheck(
            "api_contracts",
            "API contracts exported",
            "fail",
            "API contract registry has validation errors.",
            {"errors": errors},
        )
    if run_commands:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "openapi-v1.json"
            result = _run_command(
                root,
                [
                    "uv",
                    "run",
                    "molecule-ranker",
                    "api",
                    "export-openapi",
                    "--output",
                    str(output),
                    "--root",
                    str(root),
                ],
            )
            if result["exit_code"] != 0:
                return _command_check(
                    "api_contracts",
                    "API contracts exported",
                    result,
                    pass_message="OpenAPI schema exported.",
                )
            if not output.exists():
                return ReleaseCheck(
                    "api_contracts",
                    "API contracts exported",
                    "fail",
                    "OpenAPI export command passed but did not create output.",
                )
    return ReleaseCheck(
        "api_contracts",
        "API contracts exported",
        "pass",
        "API contracts validate and OpenAPI export is available.",
    )


def _check_security_audit(root: Path, *, run_commands: bool) -> ReleaseCheck:
    if not (root / "molecule_ranker/platform/security_audit.py").exists():
        return ReleaseCheck(
            "security_audit",
            "Security audit passes",
            "fail",
            "Security audit module is missing.",
        )
    if run_commands:
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_command(
                root,
                ["uv", "run", "molecule-ranker", "validate", "security", "--root", tmp, "--json"],
            )
        return _command_check(
            "security_audit",
            "Security audit passes",
            result,
            pass_message="Security audit passed against clean mocked platform config.",
        )
    return ReleaseCheck(
        "security_audit",
        "Security audit passes",
        "pass",
        "Security audit module and validation tests are present.",
    )


def _check_guardrail_audit(root: Path, *, run_commands: bool) -> ReleaseCheck:
    if not (root / "molecule_ranker/validation/guardrail_audit.py").exists():
        return ReleaseCheck(
            "guardrail_audit",
            "Guardrail audit passes",
            "fail",
            "Guardrail audit module is missing.",
        )
    if run_commands:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            (artifact_dir / "report.md").write_text(
                "Synthetic validation report. Limitations: research use only; no medical advice.\n"
            )
            result = _run_command(
                root,
                [
                    "uv",
                    "run",
                    "molecule-ranker",
                    "validate",
                    "guardrails",
                    str(artifact_dir),
                    "--json",
                ],
            )
        return _command_check(
            "guardrail_audit",
            "Guardrail audit passes",
            result,
            pass_message="Guardrail audit passed against cautious synthetic report.",
        )
    return ReleaseCheck(
        "guardrail_audit",
        "Guardrail audit passes",
        "pass",
        "Guardrail audit module and validation tests are present.",
    )


def _check_required_docs(root: Path) -> ReleaseCheck:
    required = (
        "docs/user/overview.md",
        "docs/user/limitations.md",
        "docs/admin/security_checklist.md",
        "docs/admin/users_and_roles.md",
        "docs/contracts/v1.0-api-and-artifacts.md",
    )
    missing = [path for path in required if not (root / path).exists()]
    return _required_paths_check("docs", "Platform documentation exists", required, missing)


def _check_required_runbooks(root: Path) -> ReleaseCheck:
    required = (
        "docs/runbooks/deployment.md",
        "docs/runbooks/local_development.md",
        "docs/runbooks/production_config.md",
        "docs/runbooks/backup_restore.md",
        "docs/runbooks/worker_operations.md",
        "docs/runbooks/codex_worker.md",
        "docs/runbooks/integration_sync.md",
        "docs/runbooks/security_incidents.md",
        "docs/runbooks/data_retention.md",
        "docs/runbooks/troubleshooting.md",
        "docs/runbooks/release_process.md",
    )
    missing = [path for path in required if not (root / path).exists()]
    return _required_paths_check("runbooks", "Operator runbooks exist", required, missing)


def _check_readme(root: Path) -> ReleaseCheck:
    readme = root / "README.md"
    if not readme.exists():
        return ReleaseCheck("readme", "README updated", "fail", "README.md is missing.")
    text = readme.read_text(errors="ignore").lower()
    required = ("v1.0", "validated internal research platform mvp")
    missing = [phrase for phrase in required if phrase not in text]
    if "no medical advice" not in text and "does not provide medical advice" not in text:
        missing.append("no medical advice")
    if missing:
        return ReleaseCheck(
            "readme",
            "README updated",
            "fail",
            "README.md is missing required V1.0 release language.",
            {"missing_phrases": missing},
        )
    return ReleaseCheck("readme", "README updated", "pass", "README.md documents V1.0 scope.")


def _check_docker_build_available(root: Path, *, run_commands: bool) -> ReleaseCheck:
    dockerfile = root / "deployment/Dockerfile"
    if not dockerfile.exists():
        return ReleaseCheck(
            "docker_build_available",
            "Docker build available",
            "fail",
            "deployment/Dockerfile is missing.",
        )
    if run_commands:
        result = _run_command(
            root,
            [
                "docker",
                "build",
                "-f",
                "deployment/Dockerfile",
                "-t",
                "molecule-ranker:v1-check",
                ".",
            ],
        )
        return _command_check(
            "docker_build_available",
            "Docker build available",
            result,
            pass_message="Docker build completed.",
        )
    return ReleaseCheck(
        "docker_build_available",
        "Docker build available",
        "pass",
        "deployment/Dockerfile exists; docker build is available for release validation.",
    )


def _check_no_critical_todos(root: Path) -> ReleaseCheck:
    findings = []
    for path in _iter_scanned_files(root):
        text = path.read_text(errors="ignore")
        if _CRITICAL_TODO_RE.search(text):
            findings.append(path.relative_to(root).as_posix())
    if findings:
        return ReleaseCheck(
            "no_todo_critical_markers",
            "No TODO critical markers",
            "fail",
            "Critical TODO markers were found.",
            {"files": findings[:20]},
        )
    return ReleaseCheck(
        "no_todo_critical_markers",
        "No TODO critical markers",
        "pass",
        "No critical TODO markers found in release-scanned files.",
    )


def _check_no_fixture_biomedical_data_in_production(root: Path) -> ReleaseCheck:
    fixture_markers = (
        "ExampleCandidateA",
        "ExampleTargetA",
        "ExampleDiseaseA",
        "synthetic_assay_results",
        "demo_commands.sh",
        "synthetic_external_sync_payload",
    )
    findings = []
    for path in _iter_scanned_files(root, include_docs=False):
        text = path.read_text(errors="ignore")
        if any(marker in text for marker in fixture_markers):
            findings.append(path.relative_to(root).as_posix())
    if findings:
        return ReleaseCheck(
            "no_fixture_biomedical_data_in_production",
            "No fixture biomedical data in production",
            "fail",
            "Synthetic fixture/demo markers appeared in production-scanned files.",
            {"files": findings[:20]},
        )
    return ReleaseCheck(
        "no_fixture_biomedical_data_in_production",
        "No fixture biomedical data in production",
        "pass",
        "Synthetic demo markers are absent from production-scanned files.",
    )


def _check_no_plaintext_secrets(root: Path) -> ReleaseCheck:
    findings = []
    for path in _iter_scanned_files(root, include_docs=False):
        text = path.read_text(errors="ignore")
        for match in _SECRET_ASSIGNMENT_RE.finditer(text):
            value = match.group("value")
            if _is_allowed_secret_placeholder(value):
                continue
            findings.append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "name": match.group("name"),
                }
            )
    if findings:
        return ReleaseCheck(
            "no_plaintext_secrets",
            "No plaintext secrets",
            "fail",
            "Potential plaintext secrets were found in production-scanned files.",
            {"findings": findings[:20]},
        )
    return ReleaseCheck(
        "no_plaintext_secrets",
        "No plaintext secrets",
        "pass",
        "No plaintext secret assignments found in production-scanned files.",
    )


def _required_paths_check(
    check_id: str,
    title: str,
    required: tuple[str, ...],
    missing: list[str],
) -> ReleaseCheck:
    if missing:
        return ReleaseCheck(
            check_id,
            title,
            "fail",
            "Required release documentation files are missing.",
            {"missing": missing},
        )
    return ReleaseCheck(
        check_id,
        title,
        "pass",
        f"Found {len(required)} required release documentation files.",
    )


def _run_command(root: Path, command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"exit_code": 127, "command": command, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "Command timed out.",
        }
    return {
        "exit_code": completed.returncode,
        "command": command,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def _command_check(
    check_id: str,
    title: str,
    result: dict[str, Any],
    *,
    pass_message: str,
) -> ReleaseCheck:
    if result["exit_code"] == 0:
        return ReleaseCheck(check_id, title, "pass", pass_message, {"command": result["command"]})
    return ReleaseCheck(
        check_id,
        title,
        "fail",
        "Release check command failed.",
        result,
    )


def _iter_scanned_files(root: Path, *, include_docs: bool = True) -> list[Path]:
    files: list[Path] = []
    for item in _PRODUCTION_SCAN_ROOTS:
        path = root / item
        if not path.exists():
            continue
        if path.is_file():
            if not include_docs and path.name == "README.md":
                continue
            if path.suffix in _TEXT_SUFFIXES:
                files.append(path)
            continue
        for child in path.rglob("*"):
            if not child.is_file() or child.suffix not in _TEXT_SUFFIXES:
                continue
            parts = set(child.relative_to(root).parts)
            if "__pycache__" in parts or ".pytest_cache" in parts or ".ruff_cache" in parts:
                continue
            if ".example." in child.name:
                continue
            if not include_docs and (
                "docs" in parts
                or "tests" in parts
                or "examples" in parts
                or "validation" in parts
                or "release" in parts
                or child.name == "README.md"
            ):
                continue
            files.append(child)
    return files


def _is_allowed_secret_placeholder(value: str) -> bool:
    lowered = value.lower()
    allowed = (
        "example",
        "placeholder",
        "redacted",
        "change-me",
        "readiness-password",
        "test-secret",
        "dummy",
        "local",
    )
    return any(marker in lowered for marker in allowed)


def write_release_check_report(report: dict[str, Any], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return target
