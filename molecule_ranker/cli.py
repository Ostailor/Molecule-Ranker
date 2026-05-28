from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import BaseModel

from molecule_ranker import __version__
from molecule_ranker.agents.base import AgentExecutionError, PipelineContext
from molecule_ranker.agents.developability_assessment import DevelopabilityAssessmentAgent
from molecule_ranker.agents.experiment_readiness import ExperimentReadinessAgent
from molecule_ranker.agents.oracle_scoring import OracleScoringAgent
from molecule_ranker.agents.scientific_design_planner import (
    DesignPlan,
    DesignPlanValidationError,
    ScientificDesignPlannerAgent,
)
from molecule_ranker.codex import (
    CodexArtifact,
    CodexCLIProvider,
    CodexProviderConfig,
    CodexRequest,
)
from molecule_ranker.codex_backbone.artifact_context import select_relevant_artifacts
from molecule_ranker.codex_backbone.evals import run_codex_evals
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.codex_backbone.provider import CodexBackboneProvider
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask, CodexTaskResult
from molecule_ranker.codex_engineering import (
    CodexEngineeringRunner,
    build_docs_plan_task,
    build_engineering_task,
    build_test_loop_task,
)
from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources import (
    ChEMBLAdapter,
    OpenTargetsAdapter,
    PubChemAdapter,
)
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    EvidenceRetrievalError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.design.benchmarks import DesignBenchmarkHarness
from molecule_ranker.developability.benchmark import (
    DevelopabilityBenchmarkError,
    benchmark_developability_file,
)
from molecule_ranker.experimental import (
    ActiveLearningAgent,
    ExperimentalEvidenceAgent,
    ExperimentalResultStore,
    import_assay_results,
    render_experiment_summary_markdown,
)
from molecule_ranker.experiments.active_learning import suggest_next_experiments
from molecule_ranker.experiments.importers import (
    import_assay_results_csv,
    import_assay_results_json,
)
from molecule_ranker.experiments.linking import LinkingConfig, link_assay_results
from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore as V06ExperimentalResultStore
from molecule_ranker.experiments.validation import (
    normalize_assay_result,
    validate_assay_result,
)
from molecule_ranker.generation.benchmark import (
    GenerationBenchmarkError,
    benchmark_generated_file,
)
from molecule_ranker.generation.ensemble import GeneratorEnsemble
from molecule_ranker.generation.errors import GenerationError
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    GenerationRun,
    SeedMolecule,
)
from molecule_ranker.generation.scoring import GeneratedMoleculeScorer
from molecule_ranker.literature.adapters.openalex_adapter import (
    OpenAlexAdapter as LiteratureOpenAlexAdapter,
)
from molecule_ranker.literature.adapters.pubmed_adapter import (
    PubMedAdapter as LiteraturePubMedAdapter,
)
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator
from molecule_ranker.project import (
    ProjectWorkspaceStore as LegacyProjectWorkspaceStore,
)
from molecule_ranker.project import (
    compare_project_runs,
    generate_project_dashboard,
    render_run_comparison_markdown,
)
from molecule_ranker.release import (
    build_release_manifest,
    release_manifest,
    render_release_notes,
    run_release_checks,
    write_release_notes,
)
from molecule_ranker.release.manifest import write_release_manifest
from molecule_ranker.review import (
    CodexReviewArtifact,
    DossierWriterAgent,
    FeedbackIngestionAgent,
    FollowupRequest,
    Reviewer,
    ReviewerComment,
    ReviewerDecision,
    ReviewWorkspace,
    ReviewWorkspaceStore,
    build_candidate_comparison,
    compute_review_metrics,
    generate_static_review_dashboard,
    render_static_review_dashboard,
)
from molecule_ranker.review.codex_assistant import CodexReviewAssistant
from molecule_ranker.review.comparison import render_comparison_markdown
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.dossier import render_dossier_markdown
from molecule_ranker.review.exporters import export_review_package, render_workspace_markdown
from molecule_ranker.review.queue_builder import build_review_workspace_from_artifact
from molecule_ranker.review.workspace import create_validation_handoff
from molecule_ranker.schemas import (
    Disease,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    RankingRun,
    Target,
)
from molecule_ranker.server import run_local_server
from molecule_ranker.utils import slugify
from molecule_ranker.validation import run_golden_workflows
from molecule_ranker.workspace import (
    ProjectWorkspaceStore as WorkspaceProjectStore,
)
from molecule_ranker.workspace import (
    compare_project_runs as compare_workspace_project_runs,
)
from molecule_ranker.workspace import (
    render_project_comparison_markdown,
)

PIPELINE_ERRORS = (
    DiseaseResolutionError,
    TargetDiscoveryError,
    MoleculeRetrievalError,
    EvidenceRetrievalError,
    NoCandidatesFoundError,
    ExternalDataUnavailableError,
    GenerationError,
    AgentExecutionError,
)

app = typer.Typer(
    help="Rank existing molecules for disease research hypotheses using transparent evidence.",
    no_args_is_help=True,
    context_settings={"max_content_width": 120},
)
review_app = typer.Typer(
    help="Local expert review workspace and human-in-the-loop triage commands.",
    no_args_is_help=True,
)
experimental_app = typer.Typer(
    help="Import, validate, summarize, and use experimental assay results.",
    no_args_is_help=True,
)
experiment_app = typer.Typer(
    help="V0.6 experimental assay result import, storage, review, and active learning.",
    no_args_is_help=True,
)
project_app = typer.Typer(
    help="V1.0 project workspace, sharing, jobs, dashboard, and API commands.",
    no_args_is_help=True,
)
codex_app = typer.Typer(
    help="V1.0 controlled Codex CLI assistant, worker, and engineering automation commands.",
    no_args_is_help=True,
)
codex_assist_app = typer.Typer(
    help="Artifact-grounded Codex assistant workflows.",
    no_args_is_help=True,
)
codex_engineering_app = typer.Typer(
    help="Codex-backed engineering automation and local check loops.",
    no_args_is_help=True,
)
db_app = typer.Typer(
    help="Initialize, migrate, and check the hosted platform metadata database.",
    no_args_is_help=True,
)
user_app = typer.Typer(
    help="Manage hosted platform users.",
    no_args_is_help=True,
)
auth_cli_app = typer.Typer(
    help="Manage hosted platform authentication tokens.",
    no_args_is_help=True,
)
auth_token_app = typer.Typer(
    help="Create and revoke service account tokens.",
    no_args_is_help=True,
)
config_app = typer.Typer(
    help="Show and validate production platform configuration.",
    no_args_is_help=True,
)
validate_app = typer.Typer(
    help="Run V1.0 validation suites.",
    no_args_is_help=True,
)
api_app = typer.Typer(
    help="Inspect and export frozen V1.0 hosted API contracts.",
    no_args_is_help=True,
)
worker_app = typer.Typer(
    help="Run V1.0 background workers.",
    no_args_is_help=True,
)
job_app = typer.Typer(
    help="Inspect and cancel V1.0 background jobs.",
    no_args_is_help=True,
)
notifications_app = typer.Typer(
    help="List hosted platform notifications.",
    no_args_is_help=True,
)
admin_app = typer.Typer(
    help="Inspect and control hosted platform administration.",
    no_args_is_help=True,
)
platform_cli_app = typer.Typer(
    help="Hosted platform governance, exports, deletion, and retention controls.",
    no_args_is_help=True,
)
platform_retention_app = typer.Typer(
    help="Run hosted platform data retention policies.",
    no_args_is_help=True,
)
integration_app = typer.Typer(
    help="Manage external research-system integrations.",
    no_args_is_help=True,
)
integration_system_app = typer.Typer(
    help="Manage external systems.",
    no_args_is_help=True,
)
integration_credential_app = typer.Typer(
    help="Manage integration credential references.",
    no_args_is_help=True,
)
integration_mapping_app = typer.Typer(
    help="Review and approve integration entity mappings.",
    no_args_is_help=True,
)
integration_sync_app = typer.Typer(
    help="Enqueue and inspect integration sync jobs.",
    no_args_is_help=True,
)
integration_webhook_app = typer.Typer(
    help="Test webhook signing and payload handling.",
    no_args_is_help=True,
)
integration_warehouse_app = typer.Typer(
    help="Export curated warehouse packages.",
    no_args_is_help=True,
)
integration_benchling_app = typer.Typer(
    help="Benchling connector helpers.",
    no_args_is_help=True,
)
release_app = typer.Typer(
    help="Inspect V1.0 release readiness gates and contract identifiers.",
    no_args_is_help=True,
)
design_app = typer.Typer(
    help="V1.1 target-conditioned generated molecule design workflows.",
    no_args_is_help=True,
)
app.add_typer(review_app, name="review")
app.add_typer(experimental_app, name="experimental")
app.add_typer(experiment_app, name="experiment")
app.add_typer(project_app, name="project")
app.add_typer(codex_app, name="codex")
app.add_typer(db_app, name="db")
app.add_typer(user_app, name="user")
app.add_typer(auth_cli_app, name="auth")
app.add_typer(config_app, name="config")
app.add_typer(validate_app, name="validate")
app.add_typer(api_app, name="api")
app.add_typer(worker_app, name="worker")
app.add_typer(job_app, name="job")
app.add_typer(notifications_app, name="notifications")
app.add_typer(admin_app, name="admin")
app.add_typer(platform_cli_app, name="platform")
app.add_typer(integration_app, name="integration")
app.add_typer(release_app, name="release")
app.add_typer(design_app, name="design")
codex_app.add_typer(codex_assist_app, name="assist")
codex_app.add_typer(codex_engineering_app, name="engineering")
auth_cli_app.add_typer(auth_token_app, name="token")
platform_cli_app.add_typer(platform_retention_app, name="retention")
integration_app.add_typer(integration_system_app, name="system")
integration_app.add_typer(integration_credential_app, name="credential")
integration_app.add_typer(integration_mapping_app, name="mapping")
integration_app.add_typer(integration_sync_app, name="sync")
integration_app.add_typer(integration_webhook_app, name="webhook")
integration_app.add_typer(integration_warehouse_app, name="warehouse")
integration_app.add_typer(integration_benchling_app, name="benchling")


@app.callback()
def main() -> None:
    """Agent-first molecule ranking research prototype."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@release_app.command("manifest")
def release_manifest_command(
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Optional release manifest JSON output."),
    ] = None,
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Repository root."),
    ] = Path("."),
) -> None:
    """Write or print the V1.0 release manifest."""
    manifest = build_release_manifest(root_dir)
    if output is not None:
        target = write_release_manifest(manifest, output)
        typer.echo(str(target.resolve()))
        return
    _echo_json(release_manifest(root_dir))


@release_app.command("check")
def release_check_command(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Repository root."),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
    run_commands: Annotated[
        bool,
        typer.Option(
            "--run-commands/--no-run-commands",
            help="Run expensive release checks instead of verify-only checks.",
        ),
    ] = False,
) -> None:
    """Check V1.0 release packaging and release-readiness evidence."""
    report = run_release_checks(root_dir, run_commands=run_commands)
    if json_output:
        _echo_json(report)
    else:
        typer.echo(f"Release check: {report['status']}")
        typer.echo(
            "Checks: "
            f"{report['summary']['pass']} pass, "
            f"{report['summary']['warn']} warn, "
            f"{report['summary']['fail']} fail"
        )
        for check in report["checks"]:
            if check["status"] != "pass":
                typer.echo(f"- {check['status']}: {check['check_id']}: {check['message']}")
    if report["status"] != "pass":
        raise typer.Exit(code=1)


@release_app.command("notes")
def release_notes_command(
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Release notes Markdown output."),
    ] = Path("RELEASE_NOTES.md"),
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Repository root."),
    ] = Path("."),
) -> None:
    """Write V1.0 release notes."""
    notes = render_release_notes(build_release_manifest(root_dir))
    target = write_release_notes(notes, output)
    typer.echo(str(target.resolve()))


@validate_app.command("golden")
def validate_golden_command(
    workflow: Annotated[
        str,
        typer.Option("--workflow", help="Golden workflow ID to run, or all."),
    ] = "all",
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Validation output root."),
    ] = Path("."),
    live: Annotated[
        bool,
        typer.Option("--live", help="Opt in to live validation hooks when implemented."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run deterministic V1.0 golden workflow validation."""
    output_dir = root_dir / ".molecule-ranker" / "validation" / "golden"
    try:
        report = run_golden_workflows(workflow=workflow, output_dir=output_dir, live=live)
    except KeyError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = report.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Golden workflow validation: {report.status}")
        typer.echo(f"Workflows: {report.workflow_count}")
        typer.echo(f"Report: {report.output_dir / 'golden_validation_report.md'}")
    if report.status != "pass":
        raise typer.Exit(code=1)


@validate_app.command("artifacts")
def validate_artifacts_command(
    artifact_dir: Annotated[
        Path,
        typer.Argument(file_okay=False, dir_okay=True, help="Run artifact directory to validate."),
    ],
    migrate: Annotated[
        bool,
        typer.Option(
            "--migrate/--no-migrate",
            help="Add V1.0 contract metadata to legacy JSON artifacts.",
        ),
    ] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate V1.0 artifact contracts for a run directory."""
    from molecule_ranker.contracts import validate_artifact_directory

    report = validate_artifact_directory(artifact_dir, migrate=migrate)
    payload = report.as_dict()
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Artifact contract validation: {'pass' if report.valid else 'fail'}")
        typer.echo(f"Artifacts: {report.artifact_count}")
        typer.echo(f"Migrated: {report.migrated_count}")
        for result in report.results:
            if not result.valid:
                typer.echo(f"- {result.path.name}: {'; '.join(result.errors)}")
    if not report.valid:
        raise typer.Exit(code=1)


@validate_app.command("release")
def validate_release_command(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Validation output root."),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the deterministic V1.0 release validation suite."""
    from molecule_ranker.contracts import artifact_contract_for_path, validate_artifact_file

    output_dir = root_dir / ".molecule-ranker" / "validation" / "release"
    golden_report = run_golden_workflows(workflow="all", output_dir=output_dir, live=False)
    contract_results = []
    for result in golden_report.results:
        for artifact in result.artifacts:
            if artifact_contract_for_path(artifact) is None:
                continue
            contract_results.append(validate_artifact_file(artifact, migrate=False).as_dict())
    contract_valid = all(result["valid"] for result in contract_results)
    payload = {
        "status": "pass" if golden_report.status == "pass" and contract_valid else "fail",
        "golden_status": golden_report.status,
        "workflow_count": golden_report.workflow_count,
        "live_validation": False,
        "external_services": "mocked",
        "codex_provider": "NullCodexProvider",
        "contract_artifact_count": len(contract_results),
        "contract_results": contract_results,
        "output_dir": str(output_dir.resolve()),
    }
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Release validation: {payload['status']}")
        typer.echo(f"Workflows: {payload['workflow_count']}")
        typer.echo(f"Contract artifacts: {payload['contract_artifact_count']}")
        typer.echo(f"Report: {output_dir / 'golden_validation_report.md'}")
    if payload["status"] != "pass":
        raise typer.Exit(code=1)


@validate_app.command("guardrails")
def validate_guardrails_command(
    artifact_dir: Annotated[
        Path,
        typer.Argument(file_okay=False, dir_okay=True, help="Run artifact directory to audit."),
    ],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the V1.0 guardrail audit for a run artifact directory."""
    from molecule_ranker.validation import run_guardrail_audit

    report = run_guardrail_audit(artifact_dir)
    payload = report.as_dict()
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Guardrail audit: {report.status}")
        typer.echo(f"Artifacts: {report.artifact_count}")
        typer.echo(f"Findings: {len(report.findings)}")
        typer.echo(f"JSON: {artifact_dir / 'guardrail_audit.json'}")
        typer.echo(f"Markdown: {artifact_dir / 'guardrail_audit.md'}")
        for finding in report.findings:
            typer.echo(f"- {finding.check_id}: {finding.artifact_path}: {finding.message}")
    if report.status != "pass":
        raise typer.Exit(code=1)


@validate_app.command("design")
def validate_design_command(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Validation output root."),
    ] = Path("."),
    input_artifact_dir: Annotated[
        Path | None,
        typer.Option(
            "--input-artifacts",
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Optional existing run artifact directory to use as validation input.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the deterministic V1.1 design optimization validation workflow."""
    from molecule_ranker.validation import run_design_validation

    output_dir = root_dir / ".molecule-ranker" / "validation" / "design"
    report = run_design_validation(
        output_dir=output_dir,
        input_artifact_dir=input_artifact_dir,
    )
    payload = report.as_dict()
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Design validation: {report.status}")
        typer.echo(f"Artifacts: {len(report.artifacts)}")
        typer.echo(f"Guardrail findings: {len(report.guardrail_audit.findings)}")
        typer.echo(f"JSON: {output_dir / 'design_guardrail_audit.json'}")
        typer.echo(f"Markdown: {output_dir / 'design_guardrail_audit.md'}")
    if report.status != "pass":
        raise typer.Exit(code=1)


@validate_app.command("security")
def validate_security_command(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Security audit root."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Optional hosted platform database URL."),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", dir_okay=False, help="Optional SQLite database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the V1.0 hosted-platform security release audit."""
    from molecule_ranker.platform.security_audit import run_security_audit

    report = run_security_audit(root_dir=root_dir, database_url=database_url, db_path=db_path)
    payload = report.as_dict()
    if json_output:
        _echo_json(payload)
    else:
        typer.echo(f"Security audit: {report.status}")
        typer.echo(f"Checks: {len(report.checks)}")
        typer.echo(f"Findings: {len(report.findings)}")
        typer.echo(f"JSON: {root_dir / 'security_audit.json'}")
        typer.echo(f"Markdown: {root_dir / 'security_audit.md'}")
        for finding in report.findings:
            typer.echo(f"- {finding.check_id}: {finding.location}: {finding.message}")
    if report.status != "pass":
        raise typer.Exit(code=1)


@api_app.command("export-openapi")
def api_export_openapi_command(
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Output OpenAPI JSON file."),
    ] = Path("openapi-v1.json"),
    root_dir: Annotated[
        Path,
        typer.Option(
            "--root",
            file_okay=False,
            dir_okay=True,
            help="Repository or workspace root.",
        ),
    ] = Path("."),
) -> None:
    """Export the frozen V1 OpenAPI schema."""
    from molecule_ranker.server import create_app

    target = output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app(root_dir=root_dir).openapi()
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    typer.echo(str(target))


@integration_system_app.command("create")
def integration_system_create(
    name: Annotated[str, typer.Option("--name", help="External system display name.")],
    system_type: Annotated[str, typer.Option("--system-type", help="External system type.")],
    vendor: Annotated[str | None, typer.Option("--vendor")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url")] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Default mode: read_only, dry_run, or write_enabled."),
    ] = "dry_run",
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create an external integration system."""
    from molecule_ranker.integrations.schemas import ExternalSystem
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    now = datetime.now(UTC)
    system = ExternalSystem(
        external_system_id=f"ext-{slugify(name)}",
        name=name,
        system_type=system_type,  # type: ignore[arg-type]
        vendor=vendor,
        base_url=base_url,
        enabled=True,
        default_mode=mode,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        metadata={},
    )
    try:
        created = store.create_external_system(system, org_id=org_id, project_id=project_id)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json({"system": created.model_dump(mode="json")})
        return
    typer.echo(f"Created external system: {created.external_system_id}")


@integration_system_app.command("list")
def integration_system_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List configured external systems."""
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    systems = store.list_external_systems(org_id=org_id, project_id=project_id)
    if json_output:
        _echo_json({"systems": [system.model_dump(mode="json") for system in systems]})
        return
    for system in systems:
        typer.echo(
            "\t".join(
                [
                    system.external_system_id,
                    system.name,
                    system.system_type,
                    system.vendor or "",
                    system.default_mode,
                ]
            )
        )


@integration_system_app.command("health")
def integration_system_health(
    external_system_id: Annotated[str, typer.Argument(help="External system ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a safe local health summary for an external system."""
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    system = IntegrationStore(database, org_id=org_id, project_id=project_id).get_external_system(
        external_system_id
    )
    if system is None:
        typer.echo(f"External system not found: {external_system_id}", err=True)
        raise typer.Exit(code=1)
    status = {
        "external_system_id": external_system_id,
        "status": "ok" if system.enabled else "blocked",
        "mode": system.default_mode,
        "vendor": system.vendor,
        "base_url_configured": bool(system.base_url),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if json_output:
        _echo_json(status)
        return
    typer.echo(f"{external_system_id}: {status['status']} ({system.default_mode})")


@app.command()
def health(
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            min=0.5,
            help="Short request timeout in seconds for public adapter health checks.",
        ),
    ] = 10.0,
) -> None:
    """Check public biomedical adapter reachability."""
    adapters = [
        OpenTargetsAdapter(timeout_seconds=timeout),
        ChEMBLAdapter(timeout_seconds=timeout, max_retries=2, retry_delay_seconds=0.25),
        PubChemAdapter(timeout_seconds=timeout),
        LiteraturePubMedAdapter(timeout_seconds=timeout, max_retries=0),
        LiteratureOpenAlexAdapter(timeout_seconds=timeout, max_retries=0),
    ]
    statuses = [adapter.health_check(timeout_seconds=timeout) for adapter in adapters]

    typer.echo("Source\tStatus\tLatency\tEndpoint\tError")
    for status in statuses:
        state = "OK" if status.ok else "FAIL"
        latency = f"{status.latency_ms:.1f} ms" if status.latency_ms is not None else "n/a"
        error = status.error or ""
        typer.echo(f"{status.source_name}\t{state}\t{latency}\t{status.endpoint}\t{error}")

    if not all(status.ok for status in statuses):
        raise typer.Exit(code=1)


@integration_credential_app.command("create")
def integration_credential_create(
    external_system_id: Annotated[
        str,
        typer.Option("--external-system-id", help="External system ID for this credential."),
    ],
    credential_type: Annotated[
        str,
        typer.Option(
            "--credential-type",
            help="Credential type, for example api_key or bearer_token.",
        ),
    ],
    secret_ref: Annotated[
        str | None,
        typer.Option(
            "--secret-ref",
            help=(
                "Reference only: env:NAME, local_encrypted_file:/path, "
                "or external_secret_manager:path."
            ),
        ),
    ] = None,
    secret_env_var: Annotated[
        str | None,
        typer.Option("--secret-env-var", help="Environment variable containing the secret."),
    ] = None,
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    credential_id: Annotated[
        str | None,
        typer.Option("--credential-id", help="Optional credential ID."),
    ] = None,
    actor_user_id: Annotated[
        str | None,
        typer.Option("--actor-user-id", help="Optional audit actor user ID."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a credential reference without storing the secret value."""
    from molecule_ranker.integrations.credentials import create_credential_reference

    resolved_secret_ref = secret_ref or (f"env:{secret_env_var}" if secret_env_var else None)
    if resolved_secret_ref is None:
        typer.echo("Error: provide --secret-env-var or --secret-ref.", err=True)
        raise typer.Exit(code=1)
    credential = create_credential_reference(
        external_system_id=external_system_id,
        credential_type=credential_type,  # type: ignore[arg-type]
        secret_ref=resolved_secret_ref,
        root_dir=root_dir,
        credential_id=credential_id,
        actor_user_id=actor_user_id,
    )
    payload = {"credential": credential.model_dump(mode="json")}
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Credential reference created: {credential.credential_id}")


@integration_credential_app.command("list")
def integration_credential_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    include_deleted: Annotated[
        bool,
        typer.Option("--include-deleted", help="Include revoked/deleted references."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List credential references with secret material redacted."""
    from molecule_ranker.integrations.credentials import list_credentials_redacted

    credentials = list_credentials_redacted(root_dir=root_dir, include_deleted=include_deleted)
    if json_output:
        _echo_json({"credentials": credentials})
        return
    typer.echo("Credential ID\tExternal system\tType\tSecret ref\tStatus")
    for credential in credentials:
        metadata = dict(credential.get("metadata") or {})
        typer.echo(
            "\t".join(
                [
                    str(credential["credential_id"]),
                    str(credential["external_system_id"]),
                    str(credential["credential_type"]),
                    str(credential["secret_ref"]),
                    str(metadata.get("status") or "active"),
                ]
            )
        )


@integration_credential_app.command("delete")
def integration_credential_delete(
    credential_id: Annotated[str, typer.Argument(help="Credential reference ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    actor_user_id: Annotated[
        str | None,
        typer.Option("--actor-user-id", help="Optional audit actor user ID."),
    ] = None,
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Optional deletion/revocation reason."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Revoke and hide an integration credential reference."""
    from molecule_ranker.integrations.credentials import delete_credential_reference

    credential = delete_credential_reference(
        credential_id,
        root_dir=root_dir,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    if json_output:
        _echo_json({"credential": credential})
        return
    typer.echo(f"Credential reference revoked: {credential_id}")


@integration_credential_app.command("test")
def integration_credential_test(
    credential_id: Annotated[str, typer.Argument(help="Credential reference ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate a credential reference without printing the secret."""
    from molecule_ranker.integrations.credentials import CredentialResolver

    payload = CredentialResolver(root_dir=root_dir).validate_credential(credential_id)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Credential {credential_id}: {'ok' if payload['ok'] else 'failed'}")
    typer.echo(str(payload["message"]))
    if not payload["ok"]:
        raise typer.Exit(code=1)


@integration_sync_app.command("enqueue")
def integration_sync_enqueue(
    connector_id: Annotated[str, typer.Argument(help="Integration connector ID.")],
    requested_by_user_id: Annotated[
        str,
        typer.Option("--user-id", help="User requesting the sync job."),
    ],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    direction: Annotated[str, typer.Option("--direction")] = "import",
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    object_type: Annotated[
        list[str] | None,
        typer.Option("--object-type", help="Object type to sync. Repeat for multiple types."),
    ] = None,
    job_type: Annotated[str, typer.Option("--job-type")] = "integration_sync",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Enqueue an integration sync through the V1.0 platform job queue."""
    from molecule_ranker.integrations.sync import SyncRequest
    from molecule_ranker.integrations.worker import enqueue_integration_sync_job

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    user = database.get_user(requested_by_user_id)
    if user is None:
        typer.echo(f"User not found: {requested_by_user_id}", err=True)
        raise typer.Exit(code=1)
    connector = database.get_integration_connector(connector_id)
    if connector is None:
        typer.echo(f"Connector not found: {connector_id}", err=True)
        raise typer.Exit(code=1)
    try:
        request = SyncRequest(
            direction=direction,  # type: ignore[arg-type]
            object_types=object_type or ["assay_results"],  # type: ignore[arg-type]
            mode="dry_run" if mode == "sandbox" else mode,  # type: ignore[arg-type]
            org_id=org_id,
            project_id=project_id,
            requested_by_user_id=user.user_id,
        )
        job = enqueue_integration_sync_job(
            database=database,
            connector=connector,
            request=request,
            requested_by=user,
            job_type=job_type,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json({"job": job.model_dump(mode="json")})
        return
    typer.echo(f"Enqueued integration job: {job.job_id}")


@integration_mapping_app.command("list")
def integration_mapping_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List entity mappings for review."""
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    mappings = store.find_mappings(org_id=org_id, project_id=project_id, status=status)
    if json_output:
        _echo_json({"mappings": [mapping.model_dump(mode="json") for mapping in mappings]})
        return
    for mapping in mappings:
        typer.echo(
            "\t".join(
                [
                    mapping.mapping_id,
                    mapping.internal_entity_type,
                    mapping.internal_entity_id,
                    mapping.external_ref.external_record_id,
                    mapping.status,
                ]
            )
        )


@integration_mapping_app.command("approve")
def integration_mapping_approve(
    mapping_id: Annotated[str, typer.Argument(help="Mapping ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Approve a pending mapping."""
    mapping = _update_mapping_cli(
        mapping_id,
        status="active",
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        org_id=org_id,
        project_id=project_id,
    )
    if json_output:
        _echo_json({"mapping": mapping.model_dump(mode="json")})
        return
    typer.echo(f"Approved mapping: {mapping.mapping_id}")


@integration_mapping_app.command("reject")
def integration_mapping_reject(
    mapping_id: Annotated[str, typer.Argument(help="Mapping ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Reject a pending mapping."""
    mapping = _update_mapping_cli(
        mapping_id,
        status="rejected",
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        org_id=org_id,
        project_id=project_id,
    )
    if json_output:
        _echo_json({"mapping": mapping.model_dump(mode="json")})
        return
    typer.echo(f"Rejected mapping: {mapping.mapping_id}")


@integration_sync_app.command("run")
def integration_sync_run(
    external_system_id: Annotated[
        str,
        typer.Option("--external-system-id", help="External system ID."),
    ],
    direction: Annotated[str, typer.Option("--direction")] = "import",
    object_type: Annotated[
        list[str] | None,
        typer.Option("--object-type", help="Object type to sync. Repeat for multiple types."),
    ] = None,
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    write_enabled: Annotated[bool, typer.Option("--write-enabled")] = False,
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    requested_by_user_id: Annotated[str | None, typer.Option("--user-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create and complete a guarded local sync job record."""
    from molecule_ranker.integrations.schemas import SyncJob
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    system = store.get_external_system(external_system_id)
    if system is None:
        typer.echo(f"External system not found: {external_system_id}", err=True)
        raise typer.Exit(code=1)
    mode = "write_enabled" if write_enabled else "dry_run" if dry_run else system.default_mode
    if mode == "write_enabled" and system.default_mode != "write_enabled":
        typer.echo("Error: write-enabled sync requires a write_enabled external system.", err=True)
        raise typer.Exit(code=1)
    now = datetime.now(UTC)
    try:
        job = store.create_sync_job(
            SyncJob(
                sync_job_id=f"sync-{uuid.uuid4().hex[:16]}",
                external_system_id=external_system_id,
                project_id=project_id,
                direction=direction,  # type: ignore[arg-type]
                object_types=object_type or ["assay_results"],
                mode=mode,  # type: ignore[arg-type]
                status="queued",
                requested_by_user_id=requested_by_user_id,
                metadata={"cli": True, "dry_run": mode != "write_enabled"},
            ),
            org_id=org_id,
        )
        job = store.update_sync_job(
            job.sync_job_id,
            status="succeeded",
            started_at=now,
            completed_at=datetime.now(UTC),
            records_seen=0,
            warnings=["dry-run sync record only; no connector operation performed"]
            if mode != "write_enabled"
            else [],
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json({"sync_job": job.model_dump(mode="json")})
        return
    typer.echo(f"Sync job completed: {job.sync_job_id} ({job.mode})")


@integration_sync_app.command("list")
def integration_sync_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    external_system_id: Annotated[str | None, typer.Option("--external-system-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List integration sync jobs."""
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    jobs = store.list_sync_jobs(
        org_id=org_id,
        project_id=project_id,
        external_system_id=external_system_id,
    )
    if json_output:
        _echo_json({"sync_jobs": [job.model_dump(mode="json") for job in jobs]})
        return
    for job in jobs:
        typer.echo("\t".join([job.sync_job_id, job.external_system_id, job.direction, job.status]))


@integration_sync_app.command("show")
def integration_sync_show(
    sync_job_id: Annotated[str, typer.Argument(help="Sync job ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one integration sync job and its records."""
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    job = store.get_sync_job(sync_job_id)
    if job is None:
        typer.echo(f"Sync job not found: {sync_job_id}", err=True)
        raise typer.Exit(code=1)
    records = store.list_sync_records(sync_job_id=sync_job_id)
    payload = {
        "sync_job": job.model_dump(mode="json"),
        "records": [record.model_dump(mode="json") for record in records],
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"{job.sync_job_id}: {job.status} ({len(records)} records)")


@integration_webhook_app.command("test")
def integration_webhook_test(
    external_system_id: Annotated[str, typer.Option("--external-system-id")] = "test-system",
    secret: Annotated[str, typer.Option("--secret", help="Webhook signing secret.")] = "dev-secret",
    event_type: Annotated[str, typer.Option("--event-type")] = "test.event",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate a signed generic webhook test payload."""
    from molecule_ranker.integrations.webhooks import sign_payload

    payload = {
        "webhook_event_id": f"webhook-test-{uuid.uuid4().hex[:8]}",
        "external_system_id": external_system_id,
        "event_type": event_type,
        "external_record_id": "test-record",
    }
    raw = json.dumps(payload, sort_keys=True).encode()
    result = {"payload": payload, "signature": sign_payload(raw, secret)}
    if json_output:
        _echo_json(result)
        return
    typer.echo(json.dumps(result, indent=2, sort_keys=True))


@integration_warehouse_app.command("export")
def integration_warehouse_export(
    project_id: Annotated[str, typer.Option("--project-id")],
    external_system_id: Annotated[str, typer.Option("--external-system-id")],
    tables: Annotated[str, typer.Option("--tables", help="Comma-separated table aliases.")],
    output_format: Annotated[str, typer.Option("--format")] = "csv",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path(
        ".molecule-ranker/warehouse-export"
    ),
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    org_id: Annotated[str, typer.Option("--org-id")] = "default",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export curated warehouse tables as local artifacts or SQL previews."""
    from molecule_ranker.integrations.store import IntegrationStore
    from molecule_ranker.integrations.warehouse_models import (
        build_sql_insert_upsert,
        export_rows_csv,
        export_rows_parquet,
        resolve_table_name,
    )

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    system = store.get_external_system(external_system_id)
    if system is None:
        typer.echo(f"External system not found: {external_system_id}", err=True)
        raise typer.Exit(code=1)
    if output_format == "sql" and not dry_run and system.default_mode != "write_enabled":
        typer.echo(
            "Error: SQL warehouse export requires --dry-run or write_enabled system.",
            err=True,
        )
        raise typer.Exit(code=1)
    output_root = output_dir if output_dir.is_absolute() else root_dir / output_dir
    output_root.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []
    for raw_table in [part.strip() for part in tables.split(",") if part.strip()]:
        table_name = resolve_table_name(raw_table)
        rows = [{"org_id": org_id, "project_id": project_id, "source_system": "molecule_ranker"}]
        if output_format == "csv":
            path = export_rows_csv(table_name, rows, output_root / f"{table_name}.csv")
            exported.append({"table": table_name, "path": str(path), "format": "csv"})
        elif output_format == "parquet":
            path = export_rows_parquet(table_name, rows, output_root / f"{table_name}.parquet")
            exported.append({"table": table_name, "path": str(path), "format": "parquet"})
        elif output_format == "sql":
            sql, params = build_sql_insert_upsert(table_name, rows)
            path = output_root / f"{table_name}.sql"
            path.write_text(sql + "\n-- params: " + json.dumps(params, sort_keys=True) + "\n")
            exported.append({"table": table_name, "path": str(path), "format": "sql"})
        else:
            typer.echo("Error: --format must be csv, parquet, or sql.", err=True)
            raise typer.Exit(code=1)
    if json_output:
        _echo_json(
            {
                "external_system_id": external_system_id,
                "project_id": project_id,
                "dry_run": dry_run,
                "exports": exported,
            }
        )
        return
    typer.echo(f"Warehouse export prepared: {len(exported)} table(s)")


@integration_benchling_app.command("test")
def integration_benchling_test(
    external_system_id: Annotated[str | None, typer.Option("--external-system-id")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check Benchling connector configuration without exposing credentials."""
    if dry_run:
        payload = {
            "status": "dry_run",
            "external_system_id": external_system_id,
            "base_url_configured": bool(base_url or os.getenv("BENCHLING_BASE_URL")),
            "credential_env_configured": bool(os.getenv("BENCHLING_API_KEY")),
        }
        if json_output:
            _echo_json(payload)
            return
        typer.echo(f"Benchling dry-run: {payload['status']}")
        return
    typer.echo(
        "Error: live Benchling test is not enabled by default; use connector tests.",
        err=True,
    )
    raise typer.Exit(code=1)


@integration_benchling_app.command("import-assay-results")
def integration_benchling_import_assay_results(
    external_system_id: Annotated[str, typer.Option("--external-system-id")],
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare a Benchling assay-result import task; no external read occurs in dry-run."""
    payload = {
        "external_system_id": external_system_id,
        "project_id": project_id,
        "dry_run": dry_run,
        "status": "prepared",
        "object_type": "assay_results",
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo("Benchling assay result import prepared.")


@integration_benchling_app.command("export-dossier")
def integration_benchling_export_dossier(
    external_system_id: Annotated[str, typer.Option("--external-system-id")],
    dossier_path: Annotated[Path | None, typer.Option("--dossier-path")] = None,
    write_enabled: Annotated[bool, typer.Option("--write-enabled")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare a Benchling dossier export; writes require --write-enabled."""
    if not write_enabled:
        payload = {
            "external_system_id": external_system_id,
            "dossier_path": str(dossier_path) if dossier_path else None,
            "status": "dry_run",
            "external_write": "blocked_without_write_enabled",
        }
        if json_output:
            _echo_json(payload)
            return
        typer.echo("Benchling dossier export dry-run; no external write performed.")
        return
    payload = {
        "external_system_id": external_system_id,
        "dossier_path": str(dossier_path) if dossier_path else None,
        "status": "prepared_for_write_enabled_connector",
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo("Benchling dossier export prepared for write-enabled connector.")


@app.command("serve")
def serve(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Optional local API key for non-hosted mode."),
    ] = None,
    hosted_mode: Annotated[
        bool,
        typer.Option("--hosted", help="Enable V1.0 hosted auth, RBAC, jobs, and dashboard."),
    ] = False,
    auth_secret: Annotated[
        str | None,
        typer.Option("--auth-secret", help="Hosted bearer-token signing secret."),
    ] = None,
    platform_db_path: Annotated[
        Path | None,
        typer.Option("--platform-db-path", help="Hosted SQLite platform database path."),
    ] = None,
    platform_database_url: Annotated[
        str | None,
        typer.Option("--platform-database-url", help="Hosted platform database URL."),
    ] = None,
    enable_codex_backbone: Annotated[
        bool,
        typer.Option("--enable-codex-backbone", help="Allow guarded Codex worker execution."),
    ] = False,
    allow_public_bind: Annotated[
        bool,
        typer.Option(
            "--allow-public-bind",
            help="Explicitly allow binding the API server to 0.0.0.0 or ::.",
        ),
    ] = False,
) -> None:
    """Start the molecule-ranker API server."""
    _serve_api(
        root_dir=root_dir,
        host=host,
        port=port,
        api_key=api_key,
        hosted_mode=hosted_mode,
        auth_secret=auth_secret,
        platform_db_path=platform_db_path,
        platform_database_url=platform_database_url,
        enable_codex_backbone=enable_codex_backbone,
        allow_public_bind=allow_public_bind,
    )


@db_app.command("init")
def db_init(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create the platform metadata schema if it does not exist."""
    payload = _platform_db_action(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        action="init",
    )
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Database initialized: {payload['database_url']}")
    typer.echo(f"Tables OK: {payload['ok']}")


@db_app.command("migrate")
def db_migrate(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply pending platform database migrations."""
    payload = _platform_db_action(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        action="migrate",
    )
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Database migrated: {payload['database_url']}")
    typer.echo("Applied migrations:")
    for migration in payload["applied_migrations"]:
        typer.echo(f"- {migration}")


@db_app.command("check")
def db_check(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check database connectivity, migrations, and required platform tables."""
    payload = _platform_db_action(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        action="check",
    )
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Database: {payload['database']} {payload['database_url']}")
    typer.echo(f"OK: {payload['ok']}")
    if payload["missing_tables"]:
        typer.echo("Missing tables:")
        for table in payload["missing_tables"]:
            typer.echo(f"- {table}")


@config_app.command("show")
def config_show(
    redacted: Annotated[
        bool,
        typer.Option(
            "--redacted/--no-redacted",
            help="Print secrets redacted. Raw secret printing is intentionally disabled.",
        ),
    ] = True,
) -> None:
    """Print platform settings with secret values redacted."""
    from molecule_ranker.platform.settings import PlatformSettings

    settings = PlatformSettings.from_environment()
    payload = settings.redacted_model_dump()
    payload["redacted"] = True
    if not redacted:
        payload["redaction_note"] = "Raw secret printing is disabled."
    _echo_json(payload)


@config_app.command("validate")
def config_validate() -> None:
    """Validate production platform settings."""
    from molecule_ranker.platform.settings import PlatformSettings, validate_settings

    try:
        payload = validate_settings(PlatformSettings.from_environment())
    except Exception as exc:
        typer.echo(f"Invalid configuration: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _echo_json(payload)


@worker_app.command("run")
def worker_run(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    once: Annotated[bool, typer.Option("--once", help="Run one polling pass and exit.")] = False,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval", min=0.1, help="Polling interval in seconds."),
    ] = 1.0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the SQLite/Postgres-backed platform job worker."""
    from molecule_ranker.platform.settings import PlatformSettings
    from molecule_ranker.workers import (
        CodexQueueWorker,
        IntegrationQueueWorker,
        PipelineWorker,
        WorkerScheduler,
    )
    from molecule_ranker.workspace.store import ProjectWorkspaceStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    settings = PlatformSettings.from_environment()
    workers: list[Any] = [
        IntegrationQueueWorker(database=database),
        PipelineWorker(database=database, root_dir=root_dir),
    ]
    if settings.enable_codex_worker or settings.codex_worker_enabled:
        workers.append(
            CodexQueueWorker(
                database=database,
                workspace_store=ProjectWorkspaceStore(root_dir),
                settings=settings,
            )
        )
    scheduler = WorkerScheduler(
        workers,
        poll_interval_seconds=poll_interval_seconds,
    )
    if once:
        job = scheduler.run_once()
        payload = {"job": job.model_dump(mode="json") if job else None}
        if json_output:
            _echo_json(payload)
            return
        typer.echo(f"Processed job: {job.job_id if job else 'none'}")
        return
    typer.echo("Worker polling started. Press Ctrl+C to stop.")
    scheduler.run_forever()


@job_app.command("list")
def job_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    status: Annotated[str | None, typer.Option("--status", help="Optional status filter.")] = None,
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Optional project filter."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List platform background jobs."""
    from molecule_ranker.platform.jobs import PlatformJobQueue

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    jobs = PlatformJobQueue(database).list_jobs(status=status, project_id=project_id)
    if json_output:
        _echo_json({"jobs": [job.model_dump(mode="json") for job in jobs]})
        return
    typer.echo("Job ID\tType\tStatus\tProject\tRequested by")
    for job in jobs:
        typer.echo(
            "\t".join(
                [
                    job.job_id,
                    job.job_type,
                    job.status,
                    job.project_id or "",
                    job.requested_by_user_id,
                ]
            )
        )


@job_app.command("show")
def job_show(
    job_id: Annotated[str, typer.Argument(help="Platform job ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one platform background job."""
    from molecule_ranker.platform.jobs import PlatformJobQueue

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    job = PlatformJobQueue(database).get(job_id)
    if job is None:
        typer.echo(f"Job not found: {job_id}", err=True)
        raise typer.Exit(code=1)
    if json_output:
        _echo_json(job.model_dump(mode="json"))
        return
    typer.echo(f"Job: {job.job_id}")
    typer.echo(f"Type: {job.job_type}")
    typer.echo(f"Status: {job.status}")
    typer.echo(f"Project: {job.project_id or ''}")
    typer.echo(f"Requested by: {job.requested_by_user_id}")
    typer.echo(f"Artifacts: {', '.join(job.result_artifact_ids) or 'none'}")
    if job.error_summary:
        typer.echo(f"Error: {job.error_summary}")


@job_app.command("cancel")
def job_cancel(
    job_id: Annotated[str, typer.Argument(help="Platform job ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    actor_user_id: Annotated[
        str,
        typer.Option("--actor-user-id", help="User ID recorded in the cancellation audit event."),
    ] = "cli",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Cancel a queued job, or request cooperative cancellation for a running job."""
    from molecule_ranker.platform.jobs import PlatformJobQueue

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        job = PlatformJobQueue(database).cancel(job_id, actor_user_id=actor_user_id)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(job.model_dump(mode="json"))
        return
    typer.echo(f"Job {job.job_id}: {job.status}")


@notifications_app.command("list")
def notifications_list(
    user_id: Annotated[str, typer.Option("--user-id", help="Recipient user ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    unread_only: Annotated[bool, typer.Option("--unread-only")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List hosted platform notification records for a user."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    notifications = database.list_notifications(user_id=user_id, unread_only=unread_only)
    payload = {"notifications": [item.model_dump(mode="json") for item in notifications]}
    if json_output:
        _echo_json(payload)
        return
    typer.echo("Notification ID\tType\tTitle\tProject")
    for item in notifications:
        typer.echo(
            "\t".join(
                [
                    item.notification_id,
                    item.event_type,
                    item.title,
                    item.project_id or "",
                ]
            )
        )


@admin_app.command("users")
def admin_users(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List hosted platform users without credential material."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    users = [user.model_dump(mode="json") for user in database.list_users()]
    if json_output:
        _echo_json({"users": users})
        return
    typer.echo("User ID\tEmail\tActive\tAdmin\tProvider")
    for user in users:
        typer.echo(
            "\t".join(
                [
                    str(user["user_id"]),
                    str(user["email"]),
                    str(user["is_active"]),
                    str(user["is_admin"]),
                    str(user["auth_provider"]),
                ]
            )
        )


@admin_app.command("orgs")
def admin_orgs(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List hosted platform organizations."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    orgs = [item.model_dump(mode="json") for item in database.list_organizations()]
    if json_output:
        _echo_json({"organizations": orgs})
        return
    typer.echo("Organization ID\tName\tSlug")
    for org in orgs:
        typer.echo("\t".join([str(org["org_id"]), str(org["name"]), str(org["slug"])]))


@admin_app.command("jobs")
def admin_jobs(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    failed_only: Annotated[bool, typer.Option("--failed-only")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect hosted platform jobs, including failed jobs."""
    from molecule_ranker.platform.jobs import PlatformJobQueue

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    payload = {
        "jobs": []
        if failed_only
        else [job.model_dump(mode="json") for job in PlatformJobQueue(database).list_jobs()],
        "failed_jobs": database.list_failed_jobs(),
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo("Failed jobs")
    for job in payload["failed_jobs"]:
        typer.echo(
            "\t".join(
                [
                    str(job["job_id"]),
                    str(job["job_type"]),
                    str(job["status"]),
                    str(job.get("error_summary") or ""),
                ]
            )
        )


@admin_app.command("audit")
def admin_audit(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 100,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect hosted platform audit events."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    events = [event.model_dump(mode="json") for event in database.list_audit_events(limit=limit)]
    if json_output:
        _echo_json({"events": events})
        return
    typer.echo("Timestamp\tActor\tEvent\tSummary")
    for event in events:
        typer.echo(
            "\t".join(
                [
                    str(event["timestamp"]),
                    str(event.get("actor_user_id") or ""),
                    str(event["event_type"]),
                    str(event["summary"]),
                ]
            )
        )


@admin_app.command("codex-status")
def admin_codex_status(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect hosted Codex worker queue and execution status."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    status = database.codex_worker_status()
    if json_output:
        _echo_json(status)
        return
    typer.echo(f"Queued Codex jobs: {status['queued_codex_jobs']}")
    typer.echo(f"Worker job count: {status['worker_job_count']}")
    typer.echo(f"Status counts: {status['status_counts']}")


@platform_cli_app.command("readiness")
def platform_readiness(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    environment: Annotated[str | None, typer.Option("--environment")] = None,
    artifact_storage_path: Annotated[
        Path | None,
        typer.Option("--artifact-storage-path"),
    ] = None,
    backup_path: Annotated[Path | None, typer.Option("--backup-path")] = None,
    secret_key: Annotated[
        str | None,
        typer.Option("--secret-key", envvar="MOLECULE_RANKER_SECRET_KEY"),
    ] = None,
    allowed_hosts: Annotated[
        str | None,
        typer.Option("--allowed-hosts", envvar="MOLECULE_RANKER_ALLOWED_HOSTS"),
    ] = None,
    debug: Annotated[bool | None, typer.Option("--debug/--no-debug")] = None,
    worker_enabled: Annotated[
        bool | None,
        typer.Option("--worker-enabled/--worker-disabled"),
    ] = None,
    codex_worker_enabled: Annotated[
        bool | None,
        typer.Option("--codex-worker-enabled/--codex-worker-disabled"),
    ] = None,
    external_integrations_enabled: Annotated[
        bool | None,
        typer.Option("--external-integrations-enabled/--external-integrations-disabled"),
    ] = None,
    external_credentials_valid: Annotated[
        bool | None,
        typer.Option("--external-credentials-valid/--external-credentials-invalid"),
    ] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run deployment readiness checks for a hosted platform environment."""
    from molecule_ranker.platform.readiness import run_readiness_checks

    report = run_readiness_checks(
        _readiness_config_from_cli(
            root_dir=root_dir,
            database_url=database_url,
            db_path=db_path,
            environment=environment,
            artifact_storage_path=artifact_storage_path,
            backup_path=backup_path,
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            debug=debug,
            worker_enabled=worker_enabled,
            codex_worker_enabled=codex_worker_enabled,
            external_integrations_enabled=external_integrations_enabled,
            external_credentials_valid=external_credentials_valid,
        )
    )
    _emit_readiness_report(report, json_output=json_output, output_dir=output_dir)


@platform_cli_app.command("smoke-test")
def platform_smoke_test(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the deterministic platform smoke test subset used before release."""
    from molecule_ranker.platform.readiness import run_smoke_test

    report = run_smoke_test(
        _readiness_config_from_cli(
            root_dir=root_dir,
            database_url=database_url,
            db_path=db_path,
        )
    )
    _emit_readiness_report(report, json_output=json_output, output_dir=None)


@platform_cli_app.command("doctor")
def platform_doctor(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Diagnose deployment blockers and warnings with readiness checks."""
    from molecule_ranker.platform.readiness import run_platform_doctor

    report = run_platform_doctor(
        _readiness_config_from_cli(
            root_dir=root_dir,
            database_url=database_url,
            db_path=db_path,
        )
    )
    _emit_readiness_report(report, json_output=json_output, output_dir=None)


@platform_cli_app.command("backup")
def platform_backup(
    output_path: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Backup zip path."),
    ],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    include_cache: Annotated[
        bool,
        typer.Option("--include-cache", help="Include cache files that are excluded by default."),
    ] = False,
    include_codex_transcripts: Annotated[
        bool,
        typer.Option(
            "--include-codex-transcripts",
            help="Include Codex transcript artifacts that are excluded by default.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a V1.0 internal MVP platform backup zip."""
    from molecule_ranker.platform.backup import create_platform_backup

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    result = create_platform_backup(
        database,
        output_path=output_path,
        include_cache=include_cache,
        include_codex_transcripts=include_codex_transcripts,
    )
    _emit_backup_result(result, json_output=json_output)


@platform_cli_app.command("restore")
def platform_restore(
    input_path: Annotated[
        Path,
        typer.Option("--input", exists=True, dir_okay=False, help="Backup zip path."),
    ],
    target_dir: Annotated[
        Path,
        typer.Option("--target-dir", file_okay=False, help="Restore target directory."),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate the backup and planned restore without writing."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Restore a platform backup into a target directory."""
    from molecule_ranker.platform.backup import restore_platform_backup

    result = restore_platform_backup(input_path, target_dir=target_dir, dry_run=dry_run)
    _emit_restore_result(result, json_output=json_output)


@platform_cli_app.command("backup-verify")
def platform_backup_verify(
    input_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Backup zip path."),
    ],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify a platform backup manifest and file hashes."""
    from molecule_ranker.platform.backup import verify_platform_backup

    result = verify_platform_backup(input_path)
    _emit_backup_verification_result(result, json_output=json_output)


@platform_cli_app.command("export-project")
def platform_export_project(
    project_id: Annotated[str, typer.Argument(help="Project ID to export.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    output_path: Annotated[
        Path | None,
        typer.Option("--output", help="Export zip path. Defaults under .molecule-ranker/exports."),
    ] = None,
    actor_user_id: Annotated[
        str | None,
        typer.Option("--actor-user-id", help="User ID recorded in the audit event."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Export a project package with a redacted manifest and safe artifacts."""
    from molecule_ranker.platform.export import export_project_package

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        package = export_project_package(
            database,
            project_id=project_id,
            output_path=output_path,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "project_id": package.project_id,
        "path": str(package.path),
        "artifact_count": package.artifact_count,
        "skipped_artifact_count": package.skipped_artifact_count,
        "sha256": package.sha256,
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Export written: {package.path}")
    typer.echo(f"Artifacts included: {package.artifact_count}")
    typer.echo(f"Artifacts skipped: {package.skipped_artifact_count}")


@platform_cli_app.command("delete-project")
def platform_delete_project(
    project_id: Annotated[str, typer.Argument(help="Project ID to soft-delete.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    soft: Annotated[bool, typer.Option("--soft", help="Soft-delete the project.")] = True,
    actor_user_id: Annotated[
        str | None,
        typer.Option("--actor-user-id", help="User ID recorded in the audit event."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Soft-delete a project by default so it is hidden but recoverable."""
    from molecule_ranker.platform.retention import soft_delete_project

    if not soft:
        typer.echo("Use platform purge-project for hard deletion.", err=True)
        raise typer.Exit(code=1)
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        project = soft_delete_project(
            database,
            project_id=project_id,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {"project": _json_ready(project)}
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Project soft-deleted: {project_id}")


@platform_cli_app.command("purge-project")
def platform_purge_project(
    project_id: Annotated[str, typer.Argument(help="Project ID to hard-delete.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    confirm_project_id: Annotated[
        str | None,
        typer.Option("--confirm-project-id", help="Must exactly match the project ID."),
    ] = None,
    delete_files: Annotated[
        bool,
        typer.Option("--delete-files", help="Also delete safe artifact files under the root."),
    ] = False,
    actor_user_id: Annotated[
        str | None,
        typer.Option("--actor-user-id", help="User ID recorded in the audit event."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Hard-delete project metadata after explicit project-ID confirmation."""
    from molecule_ranker.platform.retention import hard_delete_project

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        payload = hard_delete_project(
            database,
            project_id=project_id,
            confirm_project_id=confirm_project_id,
            actor_user_id=actor_user_id,
            delete_files=delete_files,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Project purged: {project_id}")


@platform_retention_app.command("run")
def platform_retention_run(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="MOLECULE_RANKER_DATABASE_URL"),
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db-path")] = None,
    artifact_retention_days: Annotated[
        int | None,
        typer.Option("--artifact-retention-days"),
    ] = None,
    codex_transcript_retention_days: Annotated[
        int | None,
        typer.Option("--codex-transcript-retention-days"),
    ] = None,
    audit_log_retention_days: Annotated[
        int | None,
        typer.Option("--audit-log-retention-days"),
    ] = None,
    cache_retention_days: Annotated[int | None, typer.Option("--cache-retention-days")] = None,
    assay_result_retention_days: Annotated[
        int | None,
        typer.Option("--assay-result-retention-days"),
    ] = None,
    actor_user_id: Annotated[
        str | None,
        typer.Option("--actor-user-id", help="User ID recorded in the audit event."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run configured retention policies; defaults perform no automatic deletion."""
    from molecule_ranker.platform.retention import DataRetentionPolicy, run_retention

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    result = run_retention(
        database,
        policy=DataRetentionPolicy(
            artifact_retention_days=artifact_retention_days,
            codex_transcript_retention_days=codex_transcript_retention_days,
            audit_log_retention_days=audit_log_retention_days,
            cache_retention_days=cache_retention_days,
            assay_result_retention_days=assay_result_retention_days,
        ),
        actor_user_id=actor_user_id,
    )
    payload = result.model_dump()
    if json_output:
        _echo_json(payload)
        return
    typer.echo("Retention run completed.")
    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


@user_app.command("create")
def user_create(
    email: Annotated[str, typer.Option("--email", help="User email address.")],
    password: Annotated[str, typer.Option("--password", help="Initial local-password credential.")],
    display_name: Annotated[
        str | None,
        typer.Option("--display-name", help="Optional display name."),
    ] = None,
    admin: Annotated[bool, typer.Option("--admin", help="Grant platform admin role.")] = False,
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a local-password platform user."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        user = database.create_user(
            email=email,
            password=password,
            display_name=display_name,
            roles=["platform_admin", "user"] if admin else ["user"],
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {"user": user.model_dump(mode="json")}
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Created user: {user.email}")
    typer.echo(f"User ID: {user.user_id}")


@user_app.command("list")
def user_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List platform users without credential material."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    users = database.list_users()
    payload = {"users": [user.model_dump(mode="json") for user in users]}
    if json_output:
        _echo_json(payload)
        return
    typer.echo("User ID\tEmail\tActive\tAdmin\tAuth provider")
    for user in users:
        typer.echo(
            "\t".join(
                [
                    user.user_id,
                    user.email,
                    str(user.is_active),
                    str(user.is_admin),
                    user.auth_provider,
                ]
            )
        )


@auth_token_app.command("create")
def auth_token_create(
    name: Annotated[str, typer.Option("--name", help="Service account token name.")],
    user_id: Annotated[str, typer.Option("--user-id", help="User/service account user ID.")],
    created_by_user_id: Annotated[
        str,
        typer.Option("--created-by-user-id", help="Admin user creating the token."),
    ],
    scope: Annotated[
        list[str] | None,
        typer.Option("--scope", help="Repeatable service token scope."),
    ] = None,
    expires_in_seconds: Annotated[
        int | None,
        typer.Option("--expires-in-seconds", min=1, help="Optional token TTL."),
    ] = None,
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a service account token. The token is shown only in this response."""
    from datetime import timedelta

    from molecule_ranker.platform.auth import generate_opaque_token

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    token = generate_opaque_token(prefix="mrs")
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
        if expires_in_seconds
        else None
    )
    try:
        token_id = database.create_service_account_token(
            name=name,
            token=token,
            user_id=user_id,
            created_by_user_id=created_by_user_id,
            scopes=scope or [],
            expires_at=expires_at,
            metadata={},
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "token_id": token_id,
        "access_token": token,
        "token_type": "bearer",
        "scopes": scope or [],
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Token ID: {token_id}")
    typer.echo(f"Token: {token}")
    typer.echo("Store this value now; it cannot be retrieved later.")


@auth_token_app.command("revoke")
def auth_token_revoke(
    token_id: Annotated[str, typer.Option("--token-id", help="Service account token ID.")],
    actor_user_id: Annotated[
        str,
        typer.Option("--actor-user-id", help="Admin user revoking the token."),
    ],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Revoke a service account token by ID."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    revoked = database.revoke_service_account_token(
        token_id=token_id,
        actor_user_id=actor_user_id,
    )
    if not revoked:
        typer.echo("Error: token not found", err=True)
        raise typer.Exit(code=1)
    payload = {"revoked": True, "token_id": token_id}
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Revoked token: {token_id}")



@experiment_app.command("import")
def experiment_import(
    input_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite experimental result database path."),
    ] = Path(".experiments/results.sqlite"),
    input_format: Annotated[
        str,
        typer.Option("--format", help="Assay result format: auto, csv, or json."),
    ] = "auto",
    imported_by: Annotated[
        str | None,
        typer.Option("--imported-by", help="Importer identity for provenance."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Raise on incomplete or ambiguous result fields."),
    ] = False,
    workspace_id: Annotated[
        str | None,
        typer.Option("--workspace-id", help="Optional review workspace identifier."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Optional ranking run identifier."),
    ] = None,
    default_disease: Annotated[
        str | None,
        typer.Option("--default-disease", help="Disease to apply when rows omit disease."),
    ] = None,
    default_target: Annotated[
        str | None,
        typer.Option("--default-target", help="Target to apply when rows omit target."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate and summarize without writing the DB."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Import CSV or JSON assay results into the V0.6 experimental result store."""
    try:
        results = _load_v06_assay_results(
            input_path,
            input_format=input_format,
            imported_by=imported_by,
        )
        results = [
            _prepare_cli_assay_result(
                result,
                strict=strict,
                workspace_id=workspace_id,
                run_id=run_id,
                default_disease=default_disease,
                default_target=default_target,
            )
            for result in results
        ]
        payload = _experiment_results_summary_payload(results)
        payload.update(
            {
                "db_path": str(db_path),
                "source_path": str(input_path),
                "dry_run": dry_run,
                "imported_count": 0 if dry_run else len(results),
            }
        )
        if not dry_run:
            V06ExperimentalResultStore(db_path).import_results(results, actor=imported_by or "cli")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(payload)
        return
    typer.echo("Experimental assay result import")
    typer.echo(f"Input: {input_path}")
    typer.echo(f"Results validated: {payload['result_count']}")
    typer.echo(f"Imported: {payload['imported_count']}")
    typer.echo(f"Outcomes: {_format_distribution(payload['outcome_counts'])}")
    if dry_run:
        typer.echo("Dry run: database was not written.")


@experiment_app.command("list")
def experiment_list(
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".experiments/results.sqlite"),
    candidate_name: Annotated[str | None, typer.Option("--candidate-name")] = None,
    target_symbol: Annotated[str | None, typer.Option("--target-symbol")] = None,
    disease_name: Annotated[str | None, typer.Option("--disease-name")] = None,
    endpoint_name: Annotated[str | None, typer.Option("--endpoint-name")] = None,
    outcome_label: Annotated[str | None, typer.Option("--outcome-label")] = None,
    qc_status: Annotated[str | None, typer.Option("--qc-status")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List imported assay results with optional filters."""
    try:
        results = V06ExperimentalResultStore(db_path).list_results(
            candidate_name=candidate_name,
            target_symbol=target_symbol,
            disease_name=disease_name,
            endpoint_name=endpoint_name,
            outcome_label=outcome_label,
            qc_status=qc_status,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "db_path": str(db_path),
        "result_count": len(results),
        "results": [result.model_dump(mode="json") for result in results],
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Experimental results: {len(results)}")
    for result in results:
        typer.echo(
            f"- {result.result_id}: {result.candidate_name} "
            f"{result.assay_context.endpoint.name} {result.outcome_label} "
            f"QC={result.qc_status}"
        )


@experiment_app.command("summarize")
def experiment_summarize(
    candidate_name: Annotated[str, typer.Option("--candidate-name")],
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".experiments/results.sqlite"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Summarize imported assay outcomes for a candidate."""
    try:
        summary = V06ExperimentalResultStore(db_path).summarize_candidate_results(
            candidate_name,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(summary.model_dump(mode="json"))
        return
    typer.echo(f"Experimental summary for {summary.candidate_name}")
    typer.echo(f"Results: {summary.result_count}")
    typer.echo(f"Positive: {summary.positive_count}")
    typer.echo(f"Negative: {summary.negative_count}")
    typer.echo(f"Failed QC: {summary.failed_qc_count}")
    typer.echo(summary.interpretation)


@experiment_app.command("link")
def experiment_link(
    from_run: Annotated[
        Path,
        typer.Option("--from-run", exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".experiments/results.sqlite"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Link stored assay results to candidates, generated hypotheses, and review items."""
    try:
        store = V06ExperimentalResultStore(db_path)
        results = store.list_results()
        candidates, generated = _load_experiment_run_candidates(from_run, include_generated=True)
        linked = link_assay_results(
            results,
            candidates=candidates,
            generated_molecules=generated,
            config=LinkingConfig(),
        )
        store.import_results(linked, actor="cli", update=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    linked_count = sum(1 for result in linked if result.metadata.get("linked_candidate_id"))
    payload = {
        "db_path": str(db_path),
        "from_run": str(from_run),
        "result_count": len(linked),
        "linked_count": linked_count,
        "unlinked_count": len(linked) - linked_count,
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Linked results: {linked_count}/{len(linked)}")


@experiment_app.command("active-learning")
def experiment_active_learning(
    from_run: Annotated[
        Path,
        typer.Option("--from-run", exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".experiments/results.sqlite"),
    strategy: Annotated[str, typer.Option("--strategy")] = "balanced",
    batch_size: Annotated[int, typer.Option("--batch-size", min=1)] = 10,
    endpoint_name: Annotated[str | None, typer.Option("--endpoint-name")] = None,
    target_symbol: Annotated[str | None, typer.Option("--target-symbol")] = None,
    include_generated: Annotated[bool, typer.Option("--include-generated")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Suggest next candidates for expert triage using imported result gaps."""
    try:
        store = V06ExperimentalResultStore(db_path)
        candidates, generated = _load_experiment_run_candidates(
            from_run,
            include_generated=include_generated,
        )
        results = store.list_results(endpoint_name=endpoint_name, target_symbol=target_symbol)
        batch = suggest_next_experiments(
            candidates,
            generated,
            results,
            [],
            {
                "strategy": strategy,
                "top_k": batch_size,
                "endpoint_name": endpoint_name,
                "target_symbol": target_symbol,
            },
        )
        store.save_active_learning_batch(batch)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(batch.model_dump(mode="json"))
        return
    typer.echo(f"Active-learning batch: {batch.batch_id}")
    for suggestion in batch.suggestions:
        typer.echo(
            f"- {suggestion.candidate_name}: {suggestion.acquisition_score:.3f} "
            f"({suggestion.acquisition_strategy})"
        )


@experiment_app.command("export")
def experiment_export(
    output: Annotated[Path, typer.Option("--output", file_okay=True, dir_okay=False)],
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".experiments/results.sqlite"),
) -> None:
    """Export stored assay results to JSON."""
    try:
        path = V06ExperimentalResultStore(db_path).export_results_json(output)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Experimental results exported: {path}")


@experiment_app.command("report")
def experiment_report(
    from_run: Annotated[
        Path,
        typer.Option("--from-run", exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".experiments/results.sqlite"),
) -> None:
    """Print a high-level experimental summary report for a run directory."""
    try:
        results = V06ExperimentalResultStore(db_path).list_results()
        candidates, generated = _load_experiment_run_candidates(from_run, include_generated=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_render_experiment_cli_report(results, candidates, generated, from_run))


@experimental_app.command("validate")
def experimental_validate(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="CSV or JSON assay result file to validate.",
        ),
    ],
    input_format: Annotated[
        str,
        typer.Option("--format", help="Assay result format: auto, csv, or json."),
    ] = "auto",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Validate and normalize assay results without persisting them."""
    try:
        imported = import_assay_results(input_path, input_format=input_format)
        report = imported.validation_report
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(report.model_dump(mode="json"))
        return
    typer.echo("Assay result validation")
    typer.echo(f"Input: {input_path}")
    typer.echo(f"Total: {report.total_count}")
    typer.echo(f"Valid: {report.valid_count}")
    typer.echo(f"Incomplete: {report.incomplete_count}")
    typer.echo(f"Invalid: {report.invalid_count}")
    typer.echo(f"Outcomes: {_format_distribution(report.outcome_counts)}")
    if report.row_issues:
        typer.echo("Rows with issues:")
        for issue in report.row_issues:
            typer.echo(f"- row {issue.get('source_row')}: {', '.join(issue['issues'])}")


@experimental_app.command("import-results")
def experimental_import_results(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="CSV or JSON assay result file to import.",
        ),
    ],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite experimental result database path."),
    ] = Path(".review/molecule-ranker-experiments.sqlite"),
    input_format: Annotated[
        str,
        typer.Option("--format", help="Assay result format: auto, csv, or json."),
    ] = "auto",
    candidates_path: Annotated[
        Path | None,
        typer.Option(
            "--candidates",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Optional ranking artifact used to link imported results to candidates.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Import assay results into the local experimental result store."""
    try:
        imported = import_assay_results(input_path, input_format=input_format)
        results = imported.results
        if candidates_path is not None:
            candidates, generated = _load_experimental_candidates(candidates_path)
            results = ExperimentalEvidenceAgent().link_results(
                results,
                candidates=candidates,
                generated_candidates=generated,
            )
        imported_count = ExperimentalResultStore(db_path).import_results(results, actor="cli")
        payload = {
            "db_path": str(db_path),
            "source_path": str(input_path),
            "imported_count": imported_count,
            "validation_report": imported.validation_report.model_dump(mode="json"),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Imported assay results: {imported_count}")
    typer.echo(f"Database: {db_path}")
    typer.echo("Experimental evidence remains separate from expert review decisions.")


@experimental_app.command("summarize")
def experimental_summarize(
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite experimental result database path."),
    ] = Path(".review/molecule-ranker-experiments.sqlite"),
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Optional Markdown summary report path.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Summarize imported experimental assay outcomes over time."""
    try:
        summary = ExperimentalResultStore(db_path).summarize()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(render_experiment_summary_markdown(summary))
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(summary.model_dump(mode="json"))
        return
    typer.echo(render_experiment_summary_markdown(summary), nl=False)
    if output_path is not None:
        typer.echo(f"Summary written: {output_path}")


@experimental_app.command("recalibrate")
def experimental_recalibrate(
    candidates_path: Annotated[
        Path,
        typer.Option(
            "--candidates",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Ranking artifact containing candidate records.",
        ),
    ],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite experimental result database path."),
    ] = Path(".review/molecule-ranker-experiments.sqlite"),
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Optional JSON recalibration report path.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Recalibrate candidate scores using only valid imported assay outcomes."""
    try:
        candidates, _generated = _load_experimental_candidates(candidates_path)
        results = ExperimentalResultStore(db_path).list_results()
        report = ExperimentalEvidenceAgent().recalibrate_candidates(candidates, results)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(report.model_dump(mode="json"))
        return
    typer.echo("Candidate score recalibration")
    for item in report.recalibrations:
        typer.echo(
            f"- {item.candidate_name}: {item.original_score} -> "
            f"{item.recalibrated_score} ({item.experimental_score_delta:+.3f})"
        )
    if output_path is not None:
        typer.echo(f"Report written: {output_path}")


@experimental_app.command("prioritize")
def experimental_prioritize(
    candidates_path: Annotated[
        Path,
        typer.Option(
            "--candidates",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Ranking artifact containing candidate records.",
        ),
    ],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite experimental result database path."),
    ] = Path(".review/molecule-ranker-experiments.sqlite"),
    top: Annotated[
        int,
        typer.Option("--top", min=1, help="Number of candidates to recommend."),
    ] = 10,
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Optional JSON active-learning report path.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Suggest next candidates to test from score uncertainty and imported outcomes."""
    try:
        candidates, _generated = _load_experimental_candidates(candidates_path)
        results = ExperimentalResultStore(db_path).list_results()
        report = ActiveLearningAgent().recommend_next_candidates(candidates, results, top=top)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(report.model_dump(mode="json"))
        return
    typer.echo("Active-learning candidate priorities")
    for recommendation in report.recommendations:
        typer.echo(
            f"- {recommendation.candidate_name}: {recommendation.priority_score:.3f} "
            f"({recommendation.evidence_gap})"
        )
    if output_path is not None:
        typer.echo(f"Report written: {output_path}")


@project_app.command("init")
def project_init(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Optional stable project identifier."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create or load a legacy project workspace manifest."""
    try:
        store = LegacyProjectWorkspaceStore(root_dir)
        workspace = store.load_or_create(project_id=project_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(workspace.model_dump(mode="json"))
        return
    typer.echo(f"Project workspace: {workspace.project_id}")
    typer.echo(f"Root: {workspace.root_dir}")
    typer.echo(f"Manifest: {store.workspace_path}")


@project_app.command("create")
def project_create(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    workspace_id: Annotated[
        str | None,
        typer.Option("--workspace-id", help="Optional stable workspace identifier."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Human-readable project workspace name."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create or load a project workspace."""
    try:
        store = WorkspaceProjectStore(root_dir)
        workspace = store.load_or_create(workspace_id=workspace_id, name=name)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(workspace.model_dump(mode="json"))
        return
    typer.echo(f"Project workspace: {workspace.workspace_id}")
    typer.echo(f"Name: {workspace.name}")
    typer.echo(f"Root: {workspace.root_dir}")
    typer.echo(f"Manifest: {store.workspace_path}")


@project_app.command("show")
def project_show(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a project workspace manifest."""
    try:
        workspace = WorkspaceProjectStore(root_dir).load_or_create()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(workspace.model_dump(mode="json"))
        return
    typer.echo(f"Project workspace: {workspace.workspace_id}")
    typer.echo(f"Name: {workspace.name}")
    typer.echo(f"Runs: {len(workspace.runs)}")
    typer.echo(f"Artifacts: {len(workspace.artifacts)}")
    typer.echo(f"Codex outputs: {len(workspace.codex_outputs)}")


@project_app.command("register-run")
def project_register_run(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Optional run identifier override."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Register one completed molecule-ranker run directory in the project workspace."""
    try:
        store = LegacyProjectWorkspaceStore(root_dir)
        workspace = store.register_run_dir(run_dir, run_id=run_id)
        resolved_run_dir = str(run_dir.resolve())
        project_run = next(run for run in workspace.runs if run.run_dir == resolved_run_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(project_run.model_dump(mode="json"))
        return
    typer.echo(f"Registered run: {project_run.run_id}")
    typer.echo(f"Disease: {project_run.disease_name}")
    typer.echo(f"Artifacts: {len(project_run.artifacts)}")


@project_app.command("run")
def project_run(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Optional run identifier override."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Register one completed run directory in the project workspace."""
    try:
        store = WorkspaceProjectStore(root_dir)
        workspace = store.register_run_dir(run_dir, run_id=run_id)
        resolved_run_dir = str(run_dir.resolve())
        project_run = next(run for run in workspace.runs if run.run_dir == resolved_run_dir)
    except (OSError, StopIteration, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(project_run.model_dump(mode="json"))
        return
    typer.echo(f"Registered run: {project_run.run_id}")
    typer.echo(f"Disease: {project_run.disease_name}")
    typer.echo(f"Artifacts: {len(project_run.artifacts)}")


@project_app.command("list")
def project_list(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List registered project runs and artifacts."""
    try:
        workspace_store = WorkspaceProjectStore(root_dir)
        if workspace_store.workspace_path.exists():
            workspace = workspace_store.load_or_create()
        else:
            workspace = LegacyProjectWorkspaceStore(root_dir).load_or_create()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(workspace.model_dump(mode="json"))
        return
    workspace_any: Any = workspace
    project_id = (
        str(workspace_any.workspace_id)
        if hasattr(workspace_any, "workspace_id")
        else str(workspace_any.project_id)
    )
    typer.echo(f"Project: {project_id}")
    typer.echo("Run ID\tDisease\tCandidates\tGenerated\tTargets\tArtifacts")
    for run in workspace.runs:
        typer.echo(
            "\t".join(
                [
                    run.run_id,
                    run.disease_name,
                    str(run.candidate_count),
                    str(run.generated_candidate_count),
                    str(run.target_count),
                    str(len(run.artifacts)),
                ]
            )
        )


@project_app.command("artifacts")
def project_artifacts(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List registered project artifacts."""
    try:
        store = WorkspaceProjectStore(root_dir)
        workspace = store.load_or_create()
        manifest = store.artifact_manifest(workspace)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json({"workspace_id": workspace.workspace_id, "artifacts": manifest})
        return
    typer.echo(f"Project: {workspace.workspace_id}")
    typer.echo("Artifact ID\tRun ID\tType\tSize\tPath")
    for artifact in manifest:
        typer.echo(
            "\t".join(
                [
                    str(artifact["artifact_id"]),
                    str(artifact.get("run_id") or ""),
                    str(artifact["artifact_type"]),
                    str(artifact["size_bytes"]),
                    str(artifact["path"]),
                ]
            )
        )


@project_app.command("comment")
def project_comment(
    project_id: Annotated[str, typer.Option("--project-id", help="Project/workspace ID.")],
    body: Annotated[str, typer.Option("--body", help="Comment text.")],
    actor_user_id: Annotated[str, typer.Option("--actor-user-id", help="Comment author user ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    object_type: Annotated[str, typer.Option("--object-type")] = "project",
    object_id: Annotated[str | None, typer.Option("--object-id")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    candidate_id: Annotated[str | None, typer.Option("--candidate-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Add a collaboration comment; comments are not biomedical evidence."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        comment = database.add_project_comment(
            project_id=project_id,
            author_user_id=actor_user_id,
            body=body,
            object_type=object_type,
            object_id=object_id,
            run_id=run_id,
            candidate_id=candidate_id,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(comment.model_dump(mode="json"))
        return
    typer.echo(f"Comment added: {comment.comment_id}")
    typer.echo("Comment is a collaboration note, not biomedical evidence.")


@project_app.command("assign")
def project_assign(
    project_id: Annotated[str, typer.Option("--project-id", help="Project/workspace ID.")],
    assigned_to_user_id: Annotated[str, typer.Option("--assigned-to-user-id")],
    actor_user_id: Annotated[str, typer.Option("--actor-user-id")],
    object_id: Annotated[str, typer.Option("--object-id", help="Review item/candidate ID.")],
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="MOLECULE_RANKER_DATABASE_URL",
            help="SQLAlchemy database URL. Supports sqlite:/// and postgresql+psycopg://.",
        ),
    ] = None,
    db_path: Annotated[
        Path | None,
        typer.Option("--db-path", help="SQLite platform database path."),
    ] = None,
    object_type: Annotated[str, typer.Option("--object-type")] = "review_item",
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    candidate_id: Annotated[str | None, typer.Option("--candidate-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Assign a review item without granting project permissions."""
    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    try:
        assignment = database.create_assignment(
            project_id=project_id,
            assigned_to_user_id=assigned_to_user_id,
            assigned_by_user_id=actor_user_id,
            object_type=object_type,
            object_id=object_id,
            run_id=run_id,
            candidate_id=candidate_id,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(assignment.model_dump(mode="json"))
        return
    typer.echo(f"Assignment created: {assignment.assignment_id}")
    typer.echo("Assignments do not grant permissions.")


@project_app.command("compare")
def project_compare(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    run_id: Annotated[
        list[str] | None,
        typer.Option("--run-id", help="Run ID to include. Repeatable; defaults to all runs."),
    ] = None,
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare two or more registered runs using existing local artifacts."""
    try:
        workspace_store = WorkspaceProjectStore(root_dir)
        if workspace_store.workspace_path.exists():
            workspace = workspace_store.load_or_create()
            selected = _select_project_runs(workspace.runs, run_id or [])
            comparison = compare_workspace_project_runs(selected)
            rendered = render_project_comparison_markdown(comparison)
        else:
            workspace = LegacyProjectWorkspaceStore(root_dir).load_or_create()
            selected = _select_project_runs(workspace.runs, run_id or [])
            comparison = compare_project_runs(selected)
            rendered = render_run_comparison_markdown(comparison)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if json_output:
                output_path.write_text(
                    json.dumps(comparison.model_dump(mode="json"), indent=2, sort_keys=True)
                    + "\n"
                )
            else:
                output_path.write_text(rendered)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(comparison.model_dump(mode="json"))
        return
    typer.echo(rendered, nl=False)
    if output_path is not None:
        typer.echo(f"Comparison written: {output_path}")


@project_app.command("summarize")
def project_summarize(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    use_codex: Annotated[
        bool,
        typer.Option("--use-codex", help="Use the controlled Codex backbone provider."),
    ] = False,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Codex mode: enabled, dry_run, or disabled."),
    ] = "dry_run",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Summarize project state from run summaries and artifact manifests."""
    try:
        store = WorkspaceProjectStore(root_dir)
        workspace = store.load_or_create()
        if use_codex:
            config = _project_codex_config(root_dir, mode=mode)
            workspace, result, output_path = store.run_codex_project_task(
                "summarize_project",
                config=config,
            )
            payload = {
                "workspace_id": workspace.workspace_id,
                "task_type": "summarize_project",
                "status": result.status,
                "output_path": str(output_path),
                "artifact_refs": [artifact.artifact_id for artifact in workspace.artifacts],
                "output_json": result.output_json,
            }
        else:
            payload = _project_summary_payload(workspace)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(payload)
        return
    if use_codex:
        typer.echo(f"Codex project summary: {payload['status']}")
        typer.echo(f"Output: {payload['output_path']}")
        typer.echo(f"Artifact refs: {len(payload['artifact_refs'])}")
        return
    typer.echo(f"Project: {payload['workspace_id']}")
    typer.echo(f"Runs: {payload['run_count']}")
    typer.echo(f"Artifacts: {payload['artifact_count']}")


@project_app.command("plan-next")
def project_plan_next(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    use_codex: Annotated[
        bool,
        typer.Option("--use-codex", help="Use the controlled Codex backbone provider."),
    ] = False,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Codex mode: enabled, dry_run, or disabled."),
    ] = "dry_run",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Suggest safe next project actions from existing artifacts."""
    try:
        store = WorkspaceProjectStore(root_dir)
        workspace = store.load_or_create()
        if use_codex:
            config = _project_codex_config(root_dir, mode=mode)
            workspace, result, output_path = store.run_codex_project_task(
                "suggest_next_project_actions",
                config=config,
            )
            payload = {
                "workspace_id": workspace.workspace_id,
                "task_type": "suggest_next_project_actions",
                "status": result.status,
                "output_path": str(output_path),
                "artifact_refs": [artifact.artifact_id for artifact in workspace.artifacts],
                "output_json": result.output_json,
            }
        else:
            payload = {
                **_project_summary_payload(workspace),
                "recommended_actions": [
                    {
                        "action_type": "summarize",
                        "safe_cli_command": "molecule-ranker project summarize --use-codex",
                        "rationale": (
                            "Create an artifact-grounded project summary before selecting "
                            "follow-up work."
                        ),
                    }
                ],
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(payload)
        return
    if use_codex:
        typer.echo(f"Codex project next-action plan: {payload['status']}")
        typer.echo(f"Output: {payload['output_path']}")
        typer.echo(f"Artifact refs: {len(payload['artifact_refs'])}")
        return
    typer.echo("Recommended next action:")
    for action in payload["recommended_actions"]:
        typer.echo(f"- {action['safe_cli_command']}: {action['rationale']}")


@project_app.command("dashboard")
def project_dashboard(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", file_okay=False, dir_okay=True, writable=True),
    ] = Path(".molecule-ranker/dashboard"),
) -> None:
    """Generate a static project dashboard from the registered workspace."""
    try:
        workspace = LegacyProjectWorkspaceStore(root_dir).load_or_create()
        path = generate_project_dashboard(workspace, output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Project dashboard written: {path}")
    typer.echo(f"Open: {path / 'index.html'}")


@project_app.command("serve")
def project_serve(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Optional local API key for non-hosted mode."),
    ] = None,
    hosted_mode: Annotated[
        bool,
        typer.Option("--hosted", help="Enable V1.0 hosted auth, RBAC, jobs, and dashboard."),
    ] = False,
    auth_secret: Annotated[
        str | None,
        typer.Option("--auth-secret", help="Hosted bearer-token signing secret."),
    ] = None,
    platform_db_path: Annotated[
        Path | None,
        typer.Option("--platform-db-path", help="Hosted SQLite platform database path."),
    ] = None,
    platform_database_url: Annotated[
        str | None,
        typer.Option("--platform-database-url", help="Hosted platform database URL."),
    ] = None,
    enable_codex_backbone: Annotated[
        bool,
        typer.Option("--enable-codex-backbone", help="Allow guarded Codex worker execution."),
    ] = False,
    allow_public_bind: Annotated[
        bool,
        typer.Option(
            "--allow-public-bind",
            help="Explicitly allow binding the API server to 0.0.0.0 or ::.",
        ),
    ] = False,
) -> None:
    """Start the project API server; hosted mode enables the V1.0 platform surface."""
    _serve_api(
        root_dir=root_dir,
        host=host,
        port=port,
        enable_codex_backbone=enable_codex_backbone,
        api_key=api_key,
        hosted_mode=hosted_mode,
        auth_secret=auth_secret,
        platform_database_url=platform_database_url,
        platform_db_path=platform_db_path,
        allow_public_bind=allow_public_bind,
    )


@codex_app.command("status")
def codex_status(
    command: Annotated[
        str,
        typer.Option("--command", help="Codex CLI command or absolute path."),
    ] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check Codex CLI availability without exposing credentials."""
    try:
        command_parts = shlex.split(command) or ["codex"]
        resolved = shutil.which(command_parts[0])
        version_check = _codex_status_check(command_parts, resolved)
        payload = {
            "configured_command": command,
            "resolved_command": resolved,
            "cli_exists": resolved is not None,
            "backbone_enabled": RankerConfig().enable_codex_backbone,
            "status_check": version_check,
        }
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Configured command: {payload['configured_command']}")
    typer.echo(f"Resolved command: {payload['resolved_command'] or 'not found'}")
    typer.echo(f"CLI exists: {payload['cli_exists']}")
    typer.echo(f"Backbone enabled: {payload['backbone_enabled']}")
    status_check = payload["status_check"]
    if isinstance(status_check, dict):
        typer.echo(f"Status check: {status_check['status']}")
        if status_check.get("stdout"):
            typer.echo(f"stdout: {status_check['stdout']}")
        if status_check.get("stderr"):
            typer.echo(f"stderr: {status_check['stderr']}")


@codex_app.command("run-task")
def codex_run_task(
    task_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run a serialized CodexTask through the controlled Codex backbone provider."""
    try:
        task = CodexTask.model_validate(json.loads(task_path.read_text()))
        result = _run_codex_task(task, dry_run=dry_run, command=command)
        destination = output_path or task_path.with_name(f"{task.task_id}_result.json")
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "status": result.status,
        "output_path": str(destination),
        "result": result.model_dump(mode="json"),
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex task status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("summarize-run")
def codex_summarize_run(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build and run a summarize_run task from a completed run directory."""
    try:
        task = _build_codex_run_task(run_dir, task_type="summarize_run")
        result = _run_codex_task(task, dry_run=dry_run, command=command)
        destination = output_path or run_dir / "codex_summary.json"
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex summary status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("explain-candidate")
def codex_explain_candidate(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    candidate: Annotated[str, typer.Option("--candidate", help="Candidate name to explain.")],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Explain a candidate ranking using only existing run artifacts."""
    try:
        task = _build_codex_run_task(run_dir, task_type="explain_ranking", candidate=candidate)
        result = _run_codex_task(task, dry_run=dry_run, command=command)
        destination = output_path or run_dir / f"codex_explain_{slugify(candidate)}.json"
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex candidate explanation status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("compare-runs")
def codex_compare_runs(
    run_a_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    run_b_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare two run directories using Codex-backed artifact explanation."""
    try:
        task = _build_codex_compare_runs_task(run_a_dir, run_b_dir)
        result = _run_codex_task(task, dry_run=dry_run, command=command)
        destination = output_path or Path("codex_run_comparison.json")
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex run comparison status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("plan-followup")
def codex_plan_followup(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Suggest safe molecule-ranker CLI follow-up commands from run artifacts."""
    try:
        task = _build_codex_run_task(run_dir, task_type="plan_followup_run")
        result = _run_codex_task(task, dry_run=dry_run, command=command)
        destination = output_path or run_dir / "codex_followup_plan.json"
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex follow-up plan status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("engineering-plan")
def codex_top_level_engineering_plan(
    goal: Annotated[
        str | None,
        typer.Option("--goal", help="Engineering planning goal."),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Deprecated alias for --goal."),
    ] = None,
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Allow Codex to apply edits.")] = False,
    allow_git_push: Annotated[bool, typer.Option("--allow-git-push")] = False,
    allow_deletions: Annotated[bool, typer.Option("--allow-deletions")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan codebase work with engineering guardrails. Dry-run is enabled by default."""
    try:
        resolved_goal = goal or prompt
        if not resolved_goal:
            raise ValueError("--goal is required.")
        task = build_engineering_task(
            task_type="implementation_planning",
            goal=resolved_goal,
            working_directory=cwd,
            apply=apply,
            allow_git_push=allow_git_push,
            allow_deletions=allow_deletions,
        )
        result = CodexEngineeringRunner(
            codex_command=command,
            working_directory=cwd,
        ).run(
            task,
            apply=apply,
            allow_git_push=allow_git_push,
            allow_deletions=allow_deletions,
        )
        destination = output_path or cwd / "codex_engineering_plan.json"
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex engineering plan status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("test-loop")
def codex_test_loop(
    test_output: Annotated[
        Path,
        typer.Option(
            "--test-output",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Captured test output to analyze.",
        ),
    ],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Allow Codex to apply edits.")] = False,
    allow_git_push: Annotated[bool, typer.Option("--allow-git-push")] = False,
    allow_deletions: Annotated[bool, typer.Option("--allow-deletions")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Analyze test failures with Codex engineering guardrails."""
    try:
        task = build_test_loop_task(
            test_output,
            working_directory=cwd,
            apply=apply,
            allow_git_push=allow_git_push,
            allow_deletions=allow_deletions,
        )
        result = CodexEngineeringRunner(codex_command=command, working_directory=cwd).run(
            task,
            apply=apply,
            allow_git_push=allow_git_push,
            allow_deletions=allow_deletions,
        )
        destination = output_path or cwd / "codex_test_loop.json"
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex test-loop status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("docs-plan")
def codex_docs_plan(
    section: Annotated[
        Path,
        typer.Option("--section", help="Documentation file or section path to update."),
    ],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Allow Codex to apply edits.")] = False,
    allow_git_push: Annotated[bool, typer.Option("--allow-git-push")] = False,
    allow_deletions: Annotated[bool, typer.Option("--allow-deletions")] = False,
    command: Annotated[str, typer.Option("--command")] = "codex",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan documentation updates with Codex engineering guardrails."""
    try:
        task = build_docs_plan_task(
            section,
            working_directory=cwd,
            apply=apply,
            allow_git_push=allow_git_push,
            allow_deletions=allow_deletions,
        )
        result = CodexEngineeringRunner(codex_command=command, working_directory=cwd).run(
            task,
            apply=apply,
            allow_git_push=allow_git_push,
            allow_deletions=allow_deletions,
        )
        destination = output_path or cwd / "codex_docs_plan.json"
        _write_json_model(destination, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = _codex_cli_payload(result, destination)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex docs-plan status: {result.status}")
    typer.echo(f"Result written: {destination}")


@codex_app.command("eval")
def codex_eval(
    cases: Annotated[
        Path,
        typer.Option(
            "--cases",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Codex eval cases JSON file.",
        ),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", file_okay=True, dir_okay=False, writable=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run deterministic evals for Codex-backed LLM task outputs."""
    try:
        report = run_codex_evals(cases)
        if output_path is not None:
            _write_json_model(output_path, report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    payload = report.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Codex eval cases: {report.case_count}")
    typer.echo(f"Passed: {report.passed_count}")
    typer.echo(f"Failed: {report.failed_count}")
    typer.echo("Metrics:")
    for name, value in report.metrics.items():
        typer.echo(f"- {name}: {value:.3f}")
    failing = [result for result in report.results if not result.passed]
    if failing:
        typer.echo("Failing cases:")
        for result in failing:
            typer.echo(f"- {result.case_id}: {'; '.join(result.failures)}")
    if output_path is not None:
        typer.echo(f"Eval report written: {output_path}")


@codex_assist_app.command("plan")
def codex_assist_plan(
    task: Annotated[str, typer.Argument(help="Planning task for Codex to structure.")],
    artifact: Annotated[
        list[Path] | None,
        typer.Option("--artifact", exists=True, readable=True, help="Grounding artifact path."),
    ] = None,
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode", help="enabled, dry_run, or disabled.")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Ask Codex CLI for a project plan grounded in supplied artifacts."""
    request = _codex_request(
        task=task,
        artifacts=artifact or [],
        workflow="project_planning",
        schema=_assistant_schema("plan"),
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@codex_assist_app.command("summarize-report")
def codex_assist_summarize_report(
    report_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Summarize a source-backed report without adding biomedical claims."""
    request = _codex_request(
        task="Summarize the supplied molecule-ranker report for expert review.",
        artifacts=[report_path],
        workflow="report_summarization",
        schema=_assistant_schema("summary"),
        prompt_sections={
            "instructions": [
                "Use only the supplied report artifact.",
                "Separate evidence, limitations, and follow-up questions.",
            ]
        },
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@codex_assist_app.command("compare-runs")
def codex_assist_compare_runs(
    root_dir: Annotated[
        Path,
        typer.Option("--root", file_okay=False, dir_okay=True, help="Project root directory."),
    ] = Path("."),
    run_id: Annotated[
        list[str] | None,
        typer.Option("--run-id", help="Run ID to include. Repeatable; defaults to all runs."),
    ] = None,
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Ask Codex to summarize an already computed multi-run comparison."""
    try:
        workspace = LegacyProjectWorkspaceStore(root_dir).load_or_create()
        comparison = compare_project_runs(_select_project_runs(workspace.runs, run_id or []))
        comparison_path = Path(".molecule-ranker") / "last-comparison.json"
        comparison_path.parent.mkdir(parents=True, exist_ok=True)
        comparison_path.write_text(
            json.dumps(comparison.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    request = _codex_request(
        task="Summarize the artifact-grounded multi-run comparison for review.",
        artifacts=[comparison_path],
        workflow="candidate_comparison",
        schema=_assistant_schema("comparison"),
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@codex_assist_app.command("review-questions")
def codex_assist_review_questions(
    workspace_json: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate high-level expert review questions from a review workspace artifact."""
    request = _codex_request(
        task="Draft high-level expert review questions grounded in the review workspace.",
        artifacts=[workspace_json],
        workflow="review_assistant",
        schema=_assistant_schema("review_questions"),
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@codex_assist_app.command("explain-active-learning")
def codex_assist_explain_active_learning(
    batch_json: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Explain an active-learning batch as triage rationale, not experimental instruction."""
    request = _codex_request(
        task="Explain why the active-learning batch was suggested for expert triage.",
        artifacts=[batch_json],
        workflow="active_learning_explanation",
        schema=_assistant_schema("active_learning"),
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@codex_assist_app.command("follow-up")
def codex_assist_follow_up(
    artifact: Annotated[
        list[Path],
        typer.Option("--artifact", exists=True, readable=True, help="Grounding artifact path."),
    ],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan computational follow-up tasks from existing artifacts."""
    request = _codex_request(
        task="Propose follow-up computational tasks grounded in the supplied artifacts.",
        artifacts=artifact,
        workflow="follow_up_task_planning",
        schema=_assistant_schema("follow_up"),
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@codex_engineering_app.command("check")
def codex_engineering_check(
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    skip_tests: Annotated[bool, typer.Option("--skip-tests")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run local engineering checks that Codex may orchestrate safely."""
    commands = [["uv", "run", "ruff", "check", "."]]
    commands.append(["uv", "run", "pyright"])
    if not skip_tests:
        commands.append(["uv", "run", "pytest"])
    results = [_run_engineering_command(command, cwd=cwd) for command in commands]
    payload = {
        "status": "ok" if all(result["returncode"] == 0 for result in results) else "failed",
        "commands": results,
    }
    if json_output:
        _echo_json(payload)
        if payload["status"] != "ok":
            raise typer.Exit(code=1)
        return
    for result in results:
        typer.echo(
            f"{' '.join(result['command'])}: exit {result['returncode']} "
            f"({result['duration_seconds']:.2f}s)"
        )
        if result["stderr_excerpt"]:
            typer.echo(result["stderr_excerpt"])
    if payload["status"] != "ok":
        raise typer.Exit(code=1)


@codex_engineering_app.command("plan")
def codex_engineering_plan(
    task: Annotated[str, typer.Argument(help="Engineering automation task.")],
    cwd: Annotated[Path, typer.Option("--cwd", file_okay=False, dir_okay=True)] = Path("."),
    mode: Annotated[str, typer.Option("--mode")] = "dry_run",
    timeout: Annotated[float, typer.Option("--timeout", min=1.0)] = 120.0,
    audit_log: Annotated[
        Path,
        typer.Option("--audit-log", file_okay=True, dir_okay=False),
    ] = Path(".codex/molecule-ranker/codex-cli-audit.jsonl"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Ask Codex CLI for an engineering automation plan."""
    request = _codex_request(
        task=task,
        artifacts=[],
        workflow="engineering_automation",
        schema=_assistant_schema("engineering"),
        prompt_sections={
            "allowed_actions": [
                "run tests",
                "run lint",
                "run typecheck",
                "inspect local artifacts",
                "summarize failures",
            ],
            "disallowed_actions": [
                "change biomedical scores without scoring modules",
                "invent biomedical evidence",
            ],
        },
    )
    response = _invoke_codex_request(
        request,
        mode=mode,
        cwd=cwd,
        timeout=timeout,
        audit_log=audit_log,
    )
    _print_codex_response(response, json_output=json_output)


@review_app.command("create")
def review_create(
    from_run: Annotated[
        Path,
        typer.Option(
            "--from-run",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Existing run artifact directory, for example results/<disease_slug>/.",
        ),
    ],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    reviewer_id: Annotated[
        str | None,
        typer.Option("--reviewer-id", help="Optional local reviewer ID metadata."),
    ] = None,
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local reviewer display name."),
    ] = None,
    reviewer_role: Annotated[
        str | None,
        typer.Option("--reviewer-role", help="Optional local reviewer role."),
    ] = None,
    include_generated: Annotated[
        bool,
        typer.Option(
            "--include-generated/--exclude-generated",
            help="Include generated molecule hypotheses in the review queue.",
        ),
    ] = True,
    max_review_items: Annotated[
        int,
        typer.Option("--max-review-items", min=1, help="Maximum review items to persist."),
    ] = 100,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable JSON summary."),
    ] = False,
) -> None:
    """Create a persisted review workspace from an existing run artifact directory."""
    try:
        payload = _load_review_run_artifacts(from_run, include_generated=include_generated)
        reviewer = _reviewer_from_cli(reviewer_id, reviewer_name, reviewer_role)
        workspace = build_review_workspace_from_artifact(payload, reviewer=reviewer)
        workspace.review_items = workspace.review_items[:max_review_items]
        workspace.metadata.update(
            {
                "source_run_dir": str(from_run),
                "include_generated": include_generated,
                "max_review_items": max_review_items,
                "reviewer": reviewer.model_dump(mode="json"),
            }
        )
        store = ReviewWorkspaceStore(db_path)
        workspace = store.create_workspace(workspace)
        review_queue_path = from_run / "review_queue.json"
        _write_review_queue_json(review_queue_path, workspace)
        summary = _workspace_summary_payload(workspace)
        summary.update(
            {
                "review_db_path": str(db_path),
                "review_queue_path": str(review_queue_path),
            }
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(summary)
        return
    typer.echo(f"Review workspace created: {workspace.workspace_id}")
    typer.echo(f"Disease: {workspace.disease_name}")
    typer.echo(f"Items: {len(workspace.review_items)}")
    typer.echo(f"Database: {db_path}")
    typer.echo(f"Queue JSON: {review_queue_path}")


@review_app.command("list")
def review_list(
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """List persisted review workspaces."""
    try:
        summaries = ReviewWorkspaceStore(db_path).list_workspaces()
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = {"review_db_path": str(db_path), "workspaces": [s.model_dump() for s in summaries]}
    if json_output:
        _echo_json(payload)
        return
    if not summaries:
        typer.echo("No review workspaces found.")
        return
    typer.echo("Workspace ID\tDisease\tCreated\tItems\tPending\tDecisions")
    for summary in summaries:
        typer.echo(
            "\t".join(
                [
                    summary.workspace_id,
                    summary.disease_name,
                    summary.created_at,
                    str(summary.review_item_count),
                    str(summary.pending_count),
                    str(summary.decision_count),
                ]
            )
        )


@review_app.command("show")
def review_show(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Show a review workspace summary."""
    try:
        workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = _workspace_summary_payload(workspace)
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Workspace: {workspace.workspace_id}")
    typer.echo(f"Disease: {workspace.disease_name}")
    typer.echo(f"Created: {workspace.created_at.isoformat()}")
    typer.echo(f"Review items: {len(workspace.review_items)}")
    typer.echo(f"Priority distribution: {_format_distribution(payload['priority_distribution'])}")
    typer.echo(f"Status distribution: {_format_distribution(payload['status_distribution'])}")
    typer.echo("Top pending items:")
    for item in payload["top_pending_items"]:
        typer.echo(
            f"- {item['review_item_id']}: {item['candidate_name']} "
            f"({item['priority_bucket']}, score={item['score']})"
        )


@review_app.command("item")
def review_item(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_id: Annotated[str, typer.Argument(help="Review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Show one review item with evidence, warnings, and score context."""
    try:
        workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
        item = _find_review_item(workspace, review_item_id)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = item.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Review item: {item.review_item_id}")
    typer.echo(f"Candidate: {item.candidate_name} ({item.candidate_origin})")
    typer.echo(f"Disease: {item.disease_name}")
    typer.echo(f"Targets: {', '.join(item.target_symbols) or 'n/a'}")
    typer.echo(f"Score: {item.score}  Confidence: {item.confidence}")
    typer.echo(f"Priority: {item.priority_bucket}  Status: {item.review_status}")
    typer.echo("Evidence summary:")
    typer.echo(json.dumps(item.evidence_summary, indent=2, sort_keys=True))
    typer.echo("Warnings:")
    for warning in item.warnings:
        typer.echo(f"- {warning}")
    typer.echo("Literature summary:")
    typer.echo(json.dumps(item.literature_summary, indent=2, sort_keys=True))
    typer.echo("Developability summary:")
    typer.echo(json.dumps(item.developability_summary, indent=2, sort_keys=True))


@review_app.command("init")
def review_init(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to candidates.json or generated_candidates.json.",
        ),
    ],
    output_path: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Path for review_workspace.json.",
        ),
    ],
    reviewer_id: Annotated[str, typer.Option("--reviewer-id", help="Local reviewer ID.")],
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local reviewer display name."),
    ] = None,
    dashboard_path: Annotated[
        Path | None,
        typer.Option(
            "--dashboard",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Optional static HTML review dashboard path.",
        ),
    ] = None,
) -> None:
    """Create a local V0.5 review workspace from saved ranking artifacts."""
    try:
        payload = json.loads(input_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("Review input must be a JSON object.")
        workspace = build_review_workspace_from_artifact(
            payload,
            reviewer=Reviewer(reviewer_id=reviewer_id, name=reviewer_name),
        )
        workspace.metadata["source_artifact"] = str(input_path)
        _write_review_workspace(output_path, workspace)
        if dashboard_path is not None:
            dashboard_path.parent.mkdir(parents=True, exist_ok=True)
            dashboard_path.write_text(render_static_review_dashboard(workspace))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Review workspace created")
    typer.echo(f"Items: {len(workspace.review_items)}")
    typer.echo(f"Output: {output_path}")
    if dashboard_path is not None:
        typer.echo(f"Dashboard: {dashboard_path}")


@review_app.command("decide")
def review_decide(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_id: Annotated[str, typer.Argument(help="Review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option(
            "--db-path",
            help="SQLite review database path.",
        ),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    decision: Annotated[
        str,
        typer.Option(
            "--decision",
            help=(
                "accept_for_followup, deprioritize, reject, needs_more_data, "
                "escalate_to_expert, or hold."
            ),
        ),
    ] = "hold",
    rationale: Annotated[
        str,
        typer.Option("--rationale", help="Reviewer rationale."),
    ] = "",
    reviewer_id: Annotated[
        str,
        typer.Option("--reviewer-id", help="Local reviewer ID."),
    ] = "local-reviewer",
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local reviewer display name."),
    ] = None,
    reviewer_role: Annotated[
        str | None,
        typer.Option("--reviewer-role", help="Optional local reviewer role."),
    ] = None,
    confidence: Annotated[
        float,
        typer.Option("--confidence", min=0.0, max=1.0, help="Reviewer confidence."),
    ] = 0.5,
    factor: Annotated[
        list[str] | None,
        typer.Option("--factor", help="Decision factor. Repeatable."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Append an explicit expert triage decision without overwriting prior decisions."""
    try:
        store = ReviewWorkspaceStore(db_path)
        reviewer = _reviewer_from_cli(reviewer_id, reviewer_name, reviewer_role)
        _require_cli_text(rationale, "--rationale")
        review_decision = ReviewerDecision(
            review_item_id=review_item_id,
            reviewer=reviewer,
            decision=_normalize_decision_label(decision),  # type: ignore[arg-type]
            rationale=rationale,
            confidence=confidence,
            decision_factors=factor or [],
        )
        store.add_decision(workspace_id, review_decision)
        if status := _status_for_decision(review_decision.decision):
            store.update_review_status(
                workspace_id,
                review_item_id,
                status,
                actor=reviewer.reviewer_id,
            )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = review_decision.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Decision recorded: {review_decision.decision}")
    typer.echo(f"Decision ID: {review_decision.decision_id}")
    typer.echo("Human decision remains separate from model-generated scores.")


@review_app.command("comment")
def review_comment(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_id: Annotated[str, typer.Argument(help="Review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    comment: Annotated[
        str,
        typer.Option("--comment", help="Reviewer comment text."),
    ] = "",
    comment_type: Annotated[
        str,
        typer.Option("--comment-type", help="Reviewer comment type."),
    ] = "general",
    reviewer_id: Annotated[
        str,
        typer.Option("--reviewer-id", help="Local reviewer ID."),
    ] = "local-reviewer",
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local reviewer display name."),
    ] = None,
    reviewer_role: Annotated[
        str | None,
        typer.Option("--reviewer-role", help="Optional local reviewer role."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Append a reviewer comment to a review item."""
    try:
        _require_cli_text(comment, "--comment")
        reviewer = _reviewer_from_cli(reviewer_id, reviewer_name, reviewer_role)
        review_comment_obj = ReviewerComment(
            review_item_id=review_item_id,
            reviewer=reviewer,
            comment_text=comment,
            comment_type=comment_type,  # type: ignore[arg-type]
        )
        ReviewWorkspaceStore(db_path).add_comment(workspace_id, review_comment_obj)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = review_comment_obj.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Comment added: {review_comment_obj.comment_id}")


@review_app.command("request-followup")
def review_request_followup(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_id: Annotated[str, typer.Argument(help="Review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    request_type: Annotated[
        str,
        typer.Option("--request-type", help="Follow-up request type."),
    ] = "expert_review",
    request_text: Annotated[
        str,
        typer.Option("--request-text", help="Follow-up request text."),
    ] = "",
    priority: Annotated[
        str,
        typer.Option("--priority", help="low, medium, or high."),
    ] = "medium",
    reviewer_id: Annotated[
        str,
        typer.Option("--reviewer-id", help="Local requester ID."),
    ] = "local-reviewer",
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local requester display name."),
    ] = None,
    reviewer_role: Annotated[
        str | None,
        typer.Option("--reviewer-role", help="Optional local requester role."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Append a follow-up computational or expert review request."""
    try:
        _require_cli_text(request_text, "--request-text")
        reviewer = _reviewer_from_cli(reviewer_id, reviewer_name, reviewer_role)
        request = FollowupRequest(
            review_item_id=review_item_id,
            requested_by=reviewer,
            request_type=_normalize_followup_type(request_type),  # type: ignore[arg-type]
            request_text=request_text,
            priority=priority,  # type: ignore[arg-type]
            status="open",
        )
        ReviewWorkspaceStore(db_path).add_followup_request(workspace_id, request)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = request.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Follow-up requested: {request.request_id}")


@review_app.command("export")
def review_export(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    output_path: Annotated[
        Path,
        typer.Option("--output", file_okay=True, dir_okay=True, writable=True),
    ] = Path("review_export"),
    output_format: Annotated[
        str,
        typer.Option("--format", help="json, markdown, or zip."),
    ] = "json",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable command result."),
    ] = False,
) -> None:
    """Export a review workspace package."""
    try:
        store = ReviewWorkspaceStore(db_path)
        workspace = store.get_workspace(workspace_id)
        if output_format == "json" and output_path.suffix == ".json":
            path = store.export_workspace_json(workspace_id, output_path)
            files: list[str] = [path.name]
        elif output_format == "markdown" and output_path.suffix == ".md":
            path = output_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_workspace_markdown(workspace))
            files = [path.name]
        else:
            result = export_review_package(
                workspace,
                output_path,
                output_format=output_format,
            )
            path = result.output_path
            files = result.files
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = {
        "workspace_id": workspace_id,
        "output": str(path),
        "format": output_format,
        "files": files,
    }
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Workspace exported: {path}")


@review_app.command("audit")
def review_audit(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Show the review workspace audit trail."""
    try:
        workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    events = [event.model_dump(mode="json") for event in workspace.audit_events]
    if json_output:
        _echo_json({"workspace_id": workspace_id, "audit_events": events})
        return
    typer.echo("Timestamp\tActor\tEvent\tObject\tSummary")
    for event in workspace.audit_events:
        typer.echo(
            "\t".join(
                [
                    event.timestamp.isoformat(),
                    event.actor,
                    event.event_type,
                    f"{event.object_type}:{event.object_id}",
                    event.summary,
                ]
            )
        )


@review_app.command("metrics")
def review_metrics(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Summarize local expert review workflow metrics."""
    try:
        workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
        metrics = compute_review_metrics(workspace)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    payload = metrics.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"Workspace: {metrics.workspace_id}")
    typer.echo(f"Disease: {metrics.disease_name}")
    typer.echo(f"Review items: {metrics.total_review_items}")
    typer.echo(f"Reviewed: {metrics.reviewed_count}")
    typer.echo(f"Pending: {metrics.pending_count}")
    typer.echo(f"Accepted: {metrics.accepted_count}")
    typer.echo(f"Rejected: {metrics.rejected_count}")
    typer.echo(f"Needs more data: {metrics.needs_more_data_count}")
    typer.echo(f"Feedback conflicts: {metrics.feedback_conflict_count}")


@review_app.command("follow-up")
def review_follow_up(
    workspace_path: Annotated[
        Path,
        typer.Option(
            "--workspace",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            writable=True,
            help="Path to review_workspace.json.",
        ),
    ],
    item_id: Annotated[str, typer.Option("--item-id", help="Review item identifier.")],
    reviewer_id: Annotated[str, typer.Option("--reviewer-id", help="Local reviewer ID.")],
    check_type: Annotated[
        str,
        typer.Option(
            "--check-type",
            help="Computational follow-up check type, for example literature_review or docking.",
        ),
    ],
    question: Annotated[str, typer.Option("--question", help="Question for follow-up work.")],
) -> None:
    """Request a follow-up computational check for a reviewed candidate."""
    try:
        workspace = _read_review_workspace(workspace_path)
        request = ReviewDecisionEngine().request_followup(
            workspace,
            review_item_id=item_id,
            reviewer=Reviewer(reviewer_id=reviewer_id),
            request_type=_normalize_followup_type(check_type),
            request_text=question,
            priority="medium",
        )
        _write_review_workspace(workspace_path, workspace)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Follow-up requested: {request.request_id}")


@review_app.command("compare")
def review_compare(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_ids: Annotated[
        list[str],
        typer.Argument(help="Two or more review item identifiers to compare."),
    ],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    allow_auto_recommendation: Annotated[
        bool,
        typer.Option(
            "--allow-auto-recommendation",
            help="Allow an automated note about the highest model score.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Compare review candidates side by side for expert review."""
    try:
        workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
        comparison = build_candidate_comparison(
            workspace,
            review_item_ids,
            allow_auto_recommendation=allow_auto_recommendation,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(comparison.model_dump(mode="json"))
        return
    typer.echo(render_comparison_markdown(comparison))


@review_app.command("codex-questions")
def review_codex_questions(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_id: Annotated[str, typer.Argument(help="Review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    codex_mode: Annotated[
        str,
        typer.Option(
            "--codex-mode",
            help="Codex execution mode: dry_run, enabled, or disabled.",
        ),
    ] = "dry_run",
    codex_command: Annotated[
        str,
        typer.Option("--codex-command", help="Codex CLI command or path."),
    ] = "codex",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Draft Codex-backed review questions and store them separately from decisions."""
    try:
        store = ReviewWorkspaceStore(db_path)
        workspace = store.get_workspace(workspace_id)
        artifact = _run_review_codex_assistant(
            workspace,
            db_path=db_path,
            codex_mode=codex_mode,
            codex_command=codex_command,
            action="questions",
            review_item_id=review_item_id,
        )
        store.add_codex_review_artifact(workspace_id, artifact)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(artifact.model_dump(mode="json"))
        return
    typer.echo(f"Codex review questions stored: {artifact.artifact_id}")
    typer.echo("Codex output is review assistance only; it is not a reviewer decision.")


@review_app.command("codex-summary")
def review_codex_summary(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    review_item_id: Annotated[str, typer.Argument(help="Review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    codex_mode: Annotated[
        str,
        typer.Option(
            "--codex-mode",
            help="Codex execution mode: dry_run, enabled, or disabled.",
        ),
    ] = "dry_run",
    codex_command: Annotated[
        str,
        typer.Option("--codex-command", help="Codex CLI command or path."),
    ] = "codex",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Draft a Codex-backed candidate dossier summary from existing review artifacts."""
    try:
        store = ReviewWorkspaceStore(db_path)
        workspace = store.get_workspace(workspace_id)
        artifact = _run_review_codex_assistant(
            workspace,
            db_path=db_path,
            codex_mode=codex_mode,
            codex_command=codex_command,
            action="summary",
            review_item_id=review_item_id,
        )
        store.add_codex_review_artifact(workspace_id, artifact)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(artifact.model_dump(mode="json"))
        return
    typer.echo(f"Codex dossier summary stored: {artifact.artifact_id}")
    typer.echo(
        "Codex output does not alter evidence, assay results, generated molecules, or scores."
    )


@review_app.command("codex-compare")
def review_codex_compare(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    item_a: Annotated[str, typer.Argument(help="First review item identifier.")],
    item_b: Annotated[str, typer.Argument(help="Second review item identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    codex_mode: Annotated[
        str,
        typer.Option(
            "--codex-mode",
            help="Codex execution mode: dry_run, enabled, or disabled.",
        ),
    ] = "dry_run",
    codex_command: Annotated[
        str,
        typer.Option("--codex-command", help="Codex CLI command or path."),
    ] = "codex",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Draft a Codex-backed candidate comparison for expert review."""
    try:
        store = ReviewWorkspaceStore(db_path)
        workspace = store.get_workspace(workspace_id)
        artifact = _run_review_codex_assistant(
            workspace,
            db_path=db_path,
            codex_mode=codex_mode,
            codex_command=codex_command,
            action="compare",
            item_a=item_a,
            item_b=item_b,
        )
        store.add_codex_review_artifact(workspace_id, artifact)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        _echo_json(artifact.model_dump(mode="json"))
        return
    typer.echo(f"Codex candidate comparison stored: {artifact.artifact_id}")
    typer.echo("Codex output is separate from final reviewer decisions.")


@review_app.command("dossier")
def review_dossier(
    workspace_path: Annotated[
        Path,
        typer.Option(
            "--workspace",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to review_workspace.json.",
        ),
    ],
    item_id: Annotated[str, typer.Option("--item-id", help="Review item identifier.")],
    output_path: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Path for the Markdown review dossier.",
        ),
    ],
) -> None:
    """Export a candidate review dossier."""
    try:
        workspace = _read_review_workspace(workspace_path)
        dossier = DossierWriterAgent().build_dossier(workspace, item_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_dossier_markdown(dossier))
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Dossier written: {output_path}")


@review_app.command("handoff")
def review_handoff(
    workspace_path: Annotated[
        Path,
        typer.Option(
            "--workspace",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to review_workspace.json.",
        ),
    ],
    item_id: Annotated[str, typer.Option("--item-id", help="Review item identifier.")],
    reviewer_id: Annotated[str, typer.Option("--reviewer-id", help="Local reviewer ID.")],
    output_path: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Path for validation_handoff.json.",
        ),
    ],
) -> None:
    """Create a validation handoff packet for a reviewed candidate."""
    try:
        workspace = _read_review_workspace(workspace_path)
        handoff = create_validation_handoff(
            workspace,
            review_item_id=item_id,
            evidence_packet_paths={"workspace": str(workspace_path)},
        )
        _write_review_workspace(workspace_path, workspace)
        _write_json_model(output_path, handoff)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Validation handoff written: {output_path}")


@review_app.command("ingest-feedback")
def review_ingest_feedback(
    workspace_path: Annotated[
        Path,
        typer.Option(
            "--workspace",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            writable=True,
            help="Path to review_workspace.json.",
        ),
    ],
    output_path: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Path for feedback_ingestion.json.",
        ),
    ],
) -> None:
    """Export expert feedback signals for future ranking runs."""
    try:
        workspace = _read_review_workspace(workspace_path)
        result = FeedbackIngestionAgent().build_feedback(workspace)
        _write_review_workspace(workspace_path, workspace)
        _write_json_model(output_path, result)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Feedback written: {output_path}")


@review_app.command("dashboard")
def review_dashboard(
    workspace_id: Annotated[str, typer.Argument(help="Review workspace identifier.")],
    db_path: Annotated[
        Path,
        typer.Option("--db-path", help="SQLite review database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output",
            file_okay=False,
            dir_okay=True,
            writable=True,
            help="Directory for static HTML review dashboard files.",
        ),
    ] = Path("review_dashboard"),
) -> None:
    """Generate a no-server static HTML review dashboard."""
    try:
        workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
        output_path = generate_static_review_dashboard(workspace, output_dir)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Dashboard written: {output_path}")
    typer.echo(f"Open: {output_path / 'index.html'}")


@app.command()
def rank(
    disease_name: Annotated[str, typer.Argument(help="Disease name to resolve and rank.")],
    top: Annotated[int, typer.Option("--top", min=1, help="Number of candidates to retain.")] = 20,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory where disease-specific outputs are written.",
        ),
    ] = Path("results"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable JSON summary to stdout."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Print an agent trace summary."),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            min=1.0,
            help="Request timeout in seconds for public biomedical data sources.",
        ),
    ] = 20.0,
    use_cache: Annotated[
        bool,
        typer.Option(
            "--use-cache",
            help=(
                "Use cached-real-data fallback when live requests fail. "
                "Default writes successful live responses but does not read cache."
            ),
        ),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Bypass cache reads and writes for this run."),
    ] = False,
    cache_dir: Annotated[
        Path,
        typer.Option("--cache-dir", help="Directory for successful real API response cache."),
    ] = Path(".cache/molecule-ranker"),
    cache_ttl_hours: Annotated[
        int,
        typer.Option("--cache-ttl-hours", min=1, help="Cached real response TTL in hours."),
    ] = 24,
    max_targets: Annotated[
        int | None,
        typer.Option(
            "--max-targets",
            min=1,
            help="Optional target limit applied after real target retrieval.",
        ),
    ] = None,
    max_molecules_per_target: Annotated[
        int | None,
        typer.Option(
            "--max-molecules-per-target",
            min=1,
            help="Optional molecule limit applied per target during real molecule retrieval.",
        ),
    ] = None,
    max_activity_records_per_target: Annotated[
        int | None,
        typer.Option(
            "--max-activity-records-per-target",
            min=1,
            help="Optional ChEMBL activity-record limit per mapped target.",
        ),
    ] = None,
    max_indications_per_molecule: Annotated[
        int,
        typer.Option(
            "--max-indications-per-molecule",
            min=1,
            help="Maximum ChEMBL indication records retained per molecule.",
        ),
    ] = 20,
    max_warnings_per_molecule: Annotated[
        int,
        typer.Option(
            "--max-warnings-per-molecule",
            min=1,
            help="Maximum ChEMBL warning records retained per molecule.",
        ),
    ] = 20,
    max_retries: Annotated[
        int,
        typer.Option(
            "--max-retries",
            min=0,
            help="Maximum retries for transient 429/5xx responses.",
        ),
    ] = 3,
    retry_backoff_seconds: Annotated[
        float,
        typer.Option(
            "--retry-backoff-seconds",
            min=0.0,
            help="Initial exponential backoff delay for transient API failures.",
        ),
    ] = 0.5,
    strict_enrichment: Annotated[
        bool,
        typer.Option(
            "--strict-enrichment",
            help="Record strict enrichment intent in run config for future adapter policy.",
        ),
    ] = False,
    enable_literature: Annotated[
        bool,
        typer.Option(
            "--enable-literature/--disable-literature",
            help="Enable or skip PubMed literature evidence retrieval.",
        ),
    ] = True,
    strict_literature: Annotated[
        bool,
        typer.Option(
            "--strict-literature/--no-strict-literature",
            help="Fail the run when literature retrieval is unavailable.",
        ),
    ] = False,
    literature_source: Annotated[
        list[str] | None,
        typer.Option(
            "--literature-source",
            help="Literature source to use. Repeatable; currently supports pubmed.",
        ),
    ] = None,
    openalex_enrichment: Annotated[
        bool,
        typer.Option(
            "--openalex-enrichment/--no-openalex-enrichment",
            help="Enable optional OpenAlex citation/OA/retraction enrichment.",
        ),
    ] = True,
    max_literature_queries: Annotated[
        int,
        typer.Option(
            "--max-literature-queries",
            min=1,
            help="Maximum literature queries generated per run.",
        ),
    ] = 100,
    max_papers_per_query: Annotated[
        int,
        typer.Option(
            "--max-papers-per-query",
            min=1,
            help="Maximum papers retrieved per literature query.",
        ),
    ] = 10,
    max_targets_for_literature: Annotated[
        int,
        typer.Option(
            "--max-targets-for-literature",
            min=1,
            help="Maximum targets used for literature query generation.",
        ),
    ] = 10,
    max_candidates_for_literature: Annotated[
        int,
        typer.Option(
            "--max-candidates-for-literature",
            min=1,
            help="Maximum candidates used for literature query generation.",
        ),
    ] = 20,
    ncbi_email: Annotated[
        str | None,
        typer.Option("--ncbi-email", help="Email sent to NCBI E-utilities when configured."),
    ] = None,
    ncbi_api_key_env: Annotated[
        str | None,
        typer.Option(
            "--ncbi-api-key-env",
            help="Environment variable name containing the NCBI API key.",
        ),
    ] = None,
    literature_failure_policy: Annotated[
        str,
        typer.Option(
            "--literature-failure-policy",
            help="Literature source failure policy: skip or fail.",
        ),
    ] = "skip",
    max_literature_queries_per_candidate: Annotated[
        int,
        typer.Option(
            "--max-literature-queries-per-candidate",
            min=1,
            help="Maximum PubMed queries generated per candidate.",
        ),
    ] = 3,
    max_literature_results_per_query: Annotated[
        int,
        typer.Option(
            "--max-literature-results-per-query",
            min=1,
            help="Maximum PubMed records fetched per generated literature query.",
        ),
    ] = 5,
    enable_openalex_metadata: Annotated[
        bool,
        typer.Option(
            "--enable-openalex-metadata",
            help="Enrich PubMed records with optional OpenAlex citation/OA/retraction metadata.",
        ),
    ] = False,
    enable_generation: Annotated[
        bool,
        typer.Option(
            "--enable-generation/--disable-generation",
            "--enable-novel-generation/--disable-novel-generation",
            help="Opt in to target-conditioned generated molecule hypotheses.",
        ),
    ] = False,
    strict_generation: Annotated[
        bool,
        typer.Option(
            "--strict-generation/--no-strict-generation",
            help="Fail the run when enabled generation cannot produce retained hypotheses.",
        ),
    ] = False,
    include_generated_in_main_ranking: Annotated[
        bool,
        typer.Option(
            "--include-generated-in-main-ranking/--separate-generated-ranking",
            help=(
                "Request generated hypotheses in the main ranking while preserving "
                "generated labels."
            ),
        ),
    ] = False,
    generation_method: Annotated[
        str,
        typer.Option(
            "--generation-method",
            help=(
                "Generated molecule backend to use. V1.1 defaults to generator_ensemble; "
                "selfies_mutation remains available for compatibility."
            ),
        ),
    ] = "generator_ensemble",
    generation_random_seed: Annotated[
        int | None,
        typer.Option(
            "--generation-random-seed",
            help="Optional deterministic random seed for generated molecule hypotheses.",
        ),
    ] = None,
    max_seed_molecules: Annotated[
        int,
        typer.Option("--max-seed-molecules", min=1, help="Maximum seed molecules selected."),
    ] = 20,
    max_generation_objectives: Annotated[
        int,
        typer.Option(
            "--max-generation-objectives",
            min=1,
            help="Maximum target-conditioned generation objectives.",
        ),
    ] = 10,
    generated_per_objective: Annotated[
        int,
        typer.Option(
            "--generated-per-objective",
            min=1,
            help="Generated structures requested per objective before filtering.",
        ),
    ] = 50,
    max_retained_generated: Annotated[
        int,
        typer.Option(
            "--max-retained-generated",
            min=1,
            help="Maximum retained generated molecule hypotheses.",
        ),
    ] = 50,
    reject_basic_alerts: Annotated[
        bool,
        typer.Option(
            "--reject-basic-alerts",
            help="Reject generated structures with coarse chemistry alerts.",
        ),
    ] = False,
    enable_structure_filtering: Annotated[
        bool,
        typer.Option(
            "--enable-structure-filtering/--disable-structure-filtering",
            help="Record structure-aware developability filter pass/fail fields.",
        ),
    ] = False,
    filter_developability_failures: Annotated[
        bool,
        typer.Option(
            "--filter-developability-failures",
            help="Remove candidates that fail the configured developability filter threshold.",
        ),
    ] = False,
    min_developability_score: Annotated[
        float,
        typer.Option(
            "--min-developability-score",
            min=0.0,
            max=1.0,
            help="Minimum heuristic developability score for optional filtering.",
        ),
    ] = 0.25,
    enable_developability: Annotated[
        bool,
        typer.Option(
            "--enable-developability/--disable-developability",
            help="Enable or skip V0.4 developability triage.",
        ),
    ] = True,
    strict_developability: Annotated[
        bool,
        typer.Option(
            "--strict-developability/--no-strict-developability",
            help="Fail the run when developability assessment fails for a molecule.",
        ),
    ] = False,
    developability_filter_mode: Annotated[
        str,
        typer.Option(
            "--developability-filter-mode",
            help=(
                "Developability action mode: report_only, deprioritize, "
                "filter_generated_only, or filter_all."
            ),
        ),
    ] = "filter_generated_only",
    reject_critical_alerts: Annotated[
        bool,
        typer.Option(
            "--reject-critical-alerts/--no-reject-critical-alerts",
            help="Reject molecules with critical developability alerts when filtering applies.",
        ),
    ] = True,
    reject_high_toxicity_risk: Annotated[
        bool,
        typer.Option(
            "--reject-high-toxicity-risk",
            help="Reject molecules with high toxicity-risk flags when filtering applies.",
        ),
    ] = False,
    enable_local_admet_models: Annotated[
        bool,
        typer.Option(
            "--enable-local-admet-models",
            help="Enable configured local ADMET model adapters when available.",
        ),
    ] = False,
    disable_rule_based_admet: Annotated[
        bool,
        typer.Option(
            "--disable-rule-based-admet",
            help="Disable the rule-based ADMET baseline triage.",
        ),
    ] = False,
    enable_structure_retrieval: Annotated[
        bool,
        typer.Option(
            "--enable-structure-retrieval",
            help="Enable optional target structure metadata retrieval.",
        ),
    ] = False,
    enable_docking: Annotated[
        bool,
        typer.Option(
            "--enable-docking",
            help=(
                "Enable optional docking plugin path when explicit structure inputs are "
                "available."
            ),
        ),
    ] = False,
    strict_structure_mode: Annotated[
        bool,
        typer.Option(
            "--strict-structure-mode",
            help="Fail optional structure/docking steps instead of warning when unavailable.",
        ),
    ] = False,
    max_structures_per_target: Annotated[
        int,
        typer.Option(
            "--max-structures-per-target",
            min=1,
            help="Maximum target structures considered per target for optional structure metadata.",
        ),
    ] = 5,
    max_docked_molecules: Annotated[
        int,
        typer.Option(
            "--max-docked-molecules",
            min=0,
            help="Maximum molecules sent to optional docking when docking is enabled.",
        ),
    ] = 20,
    enable_review_workflow: Annotated[
        bool,
        typer.Option(
            "--enable-review-workflow/--disable-review-workflow",
            help="Create a local expert review workspace during the run.",
        ),
    ] = False,
    review_db_path: Annotated[
        Path,
        typer.Option("--review-db-path", help="SQLite review workflow database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    reviewer_id: Annotated[
        str | None,
        typer.Option("--reviewer-id", help="Optional local reviewer ID metadata."),
    ] = None,
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local reviewer display name."),
    ] = None,
    reviewer_role: Annotated[
        str | None,
        typer.Option("--reviewer-role", help="Optional local reviewer role."),
    ] = None,
    max_review_items: Annotated[
        int,
        typer.Option("--max-review-items", min=1, help="Maximum review items to queue."),
    ] = 100,
    include_generated_in_review: Annotated[
        bool,
        typer.Option(
            "--include-generated-in-review/--exclude-generated-from-review",
            help="Include generated molecule hypotheses in the review workspace.",
        ),
    ] = True,
    generated_high_priority_allowed: Annotated[
        bool,
        typer.Option(
            "--generated-high-priority-allowed",
            help="Allow generated hypotheses to receive high-priority review buckets.",
        ),
    ] = False,
    enable_feedback_prior: Annotated[
        bool,
        typer.Option(
            "--enable-feedback-prior",
            help="Use stored expert review feedback as future ranking context.",
        ),
    ] = False,
    feedback_db_path: Annotated[
        Path,
        typer.Option("--feedback-db-path", help="SQLite expert feedback database path."),
    ] = Path(".review/molecule-ranker-feedback.sqlite"),
    generate_review_dashboard: Annotated[
        bool,
        typer.Option(
            "--generate-review-dashboard",
            help="Generate a static HTML dashboard for the review workspace.",
        ),
    ] = False,
    enable_experimental_evidence: Annotated[
        bool,
        typer.Option(
            "--enable-experimental-evidence/--disable-experimental-evidence",
            help="Use linked imported assay results from the experimental SQLite store.",
        ),
    ] = False,
    experimental_db_path: Annotated[
        Path,
        typer.Option("--experimental-db-path", help="SQLite experimental result database path."),
    ] = Path(".experiments/results.sqlite"),
    experimental_result_source_filter: Annotated[
        str | None,
        typer.Option(
            "--experimental-result-source-filter",
            help="Optional result source filter, for example csv_import or json_import.",
        ),
    ] = None,
    require_qc_passed_for_score: Annotated[
        bool,
        typer.Option(
            "--require-qc-passed-for-score/--allow-partial-qc-for-score",
            help="Require QC-passed experimental results before score support is added.",
        ),
    ] = True,
    include_inconclusive_results: Annotated[
        bool,
        typer.Option(
            "--include-inconclusive-results/--exclude-inconclusive-results",
            help="Record inconclusive imported results in experimental summaries.",
        ),
    ] = True,
    strict_experimental_linking: Annotated[
        bool,
        typer.Option(
            "--strict-experimental-linking/--allow-fuzzy-experimental-linking",
            help="Require exact experimental result links by default.",
        ),
    ] = True,
) -> None:
    """Run the V0.6 ranking pipeline with optional experimental evidence."""
    defaults = RankerConfig()
    config = RankerConfig(
        results_dir=output_dir,
        cache_dir=cache_dir,
        default_top=top,
        use_cache=not no_cache,
        allow_cached_real_data=use_cache and not no_cache,
        cache_ttl_seconds=cache_ttl_hours * 60 * 60,
        default_target_limit=max_targets or defaults.default_target_limit,
        target_source_limit=defaults.target_source_limit,
        max_molecules_per_target=(max_molecules_per_target or defaults.max_molecules_per_target),
        max_activity_records_per_target=(
            max_activity_records_per_target or defaults.max_activity_records_per_target
        ),
        max_indications_per_molecule=max_indications_per_molecule,
        max_warnings_per_molecule=max_warnings_per_molecule,
        enable_literature=enable_literature,
        strict_literature=strict_literature,
        literature_sources=literature_source or defaults.literature_sources,
        enable_openalex_enrichment=openalex_enrichment or enable_openalex_metadata,
        max_literature_queries=max_literature_queries,
        max_papers_per_query=max_papers_per_query,
        max_targets_for_literature=max_targets_for_literature,
        max_candidates_for_literature=max_candidates_for_literature,
        ncbi_tool=defaults.ncbi_tool,
        ncbi_email=ncbi_email,
        ncbi_api_key=os.getenv(ncbi_api_key_env) if ncbi_api_key_env else None,
        literature_request_timeout_seconds=timeout,
        literature_max_retries=max_retries,
        literature_cache_ttl_seconds=cache_ttl_hours * 60 * 60,
        max_literature_queries_per_candidate=max_literature_queries_per_candidate,
        max_literature_results_per_query=max_literature_results_per_query,
        literature_failure_policy=literature_failure_policy,
        enable_openalex_metadata=enable_openalex_metadata,
        request_timeout_seconds=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        strict_enrichment=strict_enrichment,
        enable_generation=enable_generation,
        strict_generation=strict_generation,
        include_generated_in_main_ranking=include_generated_in_main_ranking,
        generation_method=generation_method,
        generation_random_seed=generation_random_seed,
        max_seed_molecules=max_seed_molecules,
        max_generation_objectives=max_generation_objectives,
        generated_per_objective=generated_per_objective,
        max_retained_generated=max_retained_generated,
        reject_basic_alerts=reject_basic_alerts,
        enable_structure_filtering=enable_structure_filtering,
        filter_developability_failures=filter_developability_failures,
        min_developability_score=min_developability_score,
        enable_developability=enable_developability,
        strict_developability=strict_developability,
        developability_filter_mode=developability_filter_mode,
        reject_critical_alerts=reject_critical_alerts,
        reject_high_toxicity_risk=reject_high_toxicity_risk,
        enable_local_admet_models=enable_local_admet_models,
        enable_rule_based_admet=not disable_rule_based_admet,
        enable_structure_retrieval=enable_structure_retrieval,
        enable_docking=enable_docking,
        strict_structure_mode=strict_structure_mode,
        max_structures_per_target=max_structures_per_target,
        max_docked_molecules=max_docked_molecules,
        enable_review_workflow=enable_review_workflow,
        review_db_path=review_db_path,
        reviewer_id=reviewer_id,
        reviewer_name=reviewer_name,
        reviewer_role=reviewer_role,
        max_review_items=max_review_items,
        include_generated_in_review=include_generated_in_review,
        generated_high_priority_allowed=generated_high_priority_allowed,
        enable_feedback_prior=enable_feedback_prior,
        feedback_db_path=feedback_db_path,
        generate_review_dashboard=generate_review_dashboard,
        enable_experimental_evidence=enable_experimental_evidence,
        experimental_db_path=experimental_db_path,
        experimental_result_source_filter=experimental_result_source_filter,
        require_qc_passed_for_score=require_qc_passed_for_score,
        include_inconclusive_results=include_inconclusive_results,
        strict_experimental_linking=strict_experimental_linking,
    )

    try:
        result = MoleculeRankerOrchestrator(
            config=config,
        ).rank(
            disease_name,
            top_n=top,
            output_dir=output_dir,
        )
    except PIPELINE_ERRORS as exc:
        typer.echo(f"Error: {exc.__class__.__name__}", err=True)
        if isinstance(exc, DiseaseResolutionError) and "ambiguous" in str(exc).lower():
            typer.echo(str(exc), err=True)
            typer.echo("No report was generated.", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(str(exc), err=True)
        typer.echo("No report was generated.", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(_summary_payload(result, output_dir, verbose=verbose), indent=2))
        return

    _print_human_summary(result, output_dir, verbose=verbose)


@app.command()
def generate(
    disease_name: Annotated[
        str,
        typer.Argument(help="Disease name to resolve before generated hypotheses."),
    ],
    top: Annotated[
        int,
        typer.Option("--top", min=1, help="Number of existing candidates used as context."),
    ] = 10,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory where disease-specific outputs are written.",
        ),
    ] = Path("results"),
    max_retained_generated: Annotated[
        int,
        typer.Option(
            "--max-retained-generated",
            min=1,
            help="Maximum retained generated molecule hypotheses.",
        ),
    ] = 25,
    generation_method: Annotated[
        str,
        typer.Option(
            "--generation-method",
            help=(
                "Generated molecule backend to use. V1.1 defaults to generator_ensemble; "
                "selfies_mutation remains available for compatibility."
            ),
        ),
    ] = "generator_ensemble",
    generation_random_seed: Annotated[
        int | None,
        typer.Option(
            "--generation-random-seed",
            help="Optional deterministic random seed for generated molecule hypotheses.",
        ),
    ] = None,
    strict_generation: Annotated[
        bool,
        typer.Option(
            "--strict-generation/--no-strict-generation",
            help="Fail the run when generation cannot produce retained hypotheses.",
        ),
    ] = False,
    include_generated_in_main_ranking: Annotated[
        bool,
        typer.Option(
            "--include-generated-in-main-ranking/--separate-generated-ranking",
            help="Also include generated hypotheses in the main candidate list.",
        ),
    ] = False,
    reject_basic_alerts: Annotated[
        bool,
        typer.Option(
            "--reject-basic-alerts",
            help="Reject generated structures with coarse chemistry alerts.",
        ),
    ] = False,
    enable_structure_filtering: Annotated[
        bool,
        typer.Option(
            "--enable-structure-filtering/--disable-structure-filtering",
            help="Record structure-aware developability filter pass/fail fields.",
        ),
    ] = False,
    enable_review_workflow: Annotated[
        bool,
        typer.Option(
            "--enable-review-workflow/--disable-review-workflow",
            help="Create a local expert review workspace during generation.",
        ),
    ] = False,
    review_db_path: Annotated[
        Path,
        typer.Option("--review-db-path", help="SQLite review workflow database path."),
    ] = Path(".review/molecule-ranker-review.sqlite"),
    reviewer_id: Annotated[
        str | None,
        typer.Option("--reviewer-id", help="Optional local reviewer ID metadata."),
    ] = None,
    reviewer_name: Annotated[
        str | None,
        typer.Option("--reviewer-name", help="Optional local reviewer display name."),
    ] = None,
    reviewer_role: Annotated[
        str | None,
        typer.Option("--reviewer-role", help="Optional local reviewer role."),
    ] = None,
    max_review_items: Annotated[
        int,
        typer.Option("--max-review-items", min=1, help="Maximum review items to queue."),
    ] = 100,
    include_generated_in_review: Annotated[
        bool,
        typer.Option(
            "--include-generated-in-review/--exclude-generated-from-review",
            help="Include generated molecule hypotheses in the review workspace.",
        ),
    ] = True,
    generated_high_priority_allowed: Annotated[
        bool,
        typer.Option(
            "--generated-high-priority-allowed",
            help="Allow generated hypotheses to receive high-priority review buckets.",
        ),
    ] = False,
    enable_feedback_prior: Annotated[
        bool,
        typer.Option(
            "--enable-feedback-prior",
            help="Use stored expert review feedback as future ranking context.",
        ),
    ] = False,
    feedback_db_path: Annotated[
        Path,
        typer.Option("--feedback-db-path", help="SQLite expert feedback database path."),
    ] = Path(".review/molecule-ranker-feedback.sqlite"),
    generate_review_dashboard: Annotated[
        bool,
        typer.Option(
            "--generate-review-dashboard",
            help="Generate a static HTML dashboard for the review workspace.",
        ),
    ] = False,
) -> None:
    """Run the full retrieval pipeline and focus output on generated molecules."""
    config = RankerConfig(
        results_dir=output_dir,
        default_top=top,
        enable_generation=True,
        strict_generation=strict_generation,
        include_generated_in_main_ranking=include_generated_in_main_ranking,
        generation_method=generation_method,
        generation_random_seed=generation_random_seed,
        max_retained_generated=max_retained_generated,
        reject_basic_alerts=reject_basic_alerts,
        enable_structure_filtering=enable_structure_filtering,
        enable_review_workflow=enable_review_workflow,
        review_db_path=review_db_path,
        reviewer_id=reviewer_id,
        reviewer_name=reviewer_name,
        reviewer_role=reviewer_role,
        max_review_items=max_review_items,
        include_generated_in_review=include_generated_in_review,
        generated_high_priority_allowed=generated_high_priority_allowed,
        enable_feedback_prior=enable_feedback_prior,
        feedback_db_path=feedback_db_path,
        generate_review_dashboard=generate_review_dashboard,
    )

    try:
        result = MoleculeRankerOrchestrator(config=config).rank(
            disease_name,
            top_n=top,
            output_dir=output_dir,
        )
    except PIPELINE_ERRORS as exc:
        typer.echo(f"Error: {exc.__class__.__name__}", err=True)
        typer.echo(str(exc), err=True)
        typer.echo("No report was generated.", err=True)
        raise typer.Exit(code=1) from exc

    _print_generation_summary(result, output_dir)


@app.command()
def benchmark_generation(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to generated_candidates.json.",
        ),
    ],
) -> None:
    """Benchmark generated molecule artifact quality with internal V0.3 metrics."""
    try:
        result = benchmark_generated_file(input_path)
    except GenerationBenchmarkError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Generation benchmark summary")
    typer.echo(f"Input: {input_path}")
    typer.echo(f"Validity rate: {result.validity_rate:.3f}")
    typer.echo(f"Uniqueness rate: {result.uniqueness_rate:.3f}")
    typer.echo(f"Novelty rate: {result.novelty_rate:.3f}")
    typer.echo(f"Near-duplicate rate: {result.near_duplicate_rate:.3f}")
    typer.echo(f"Retained rate: {result.retained_rate:.3f}")
    typer.echo(f"Diversity clusters: {result.diversity_cluster_count}")
    typer.echo("")
    typer.echo("JSON summary:")
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


@design_app.command("plan")
def design_plan_command(
    run_dir: Annotated[
        Path,
        typer.Option(
            "--run-dir",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Directory containing run artifacts such as candidates.json.",
        ),
    ] = Path("."),
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, dir_okay=True, help="Output directory."),
    ] = None,
    use_codex_planner: Annotated[
        bool,
        typer.Option(
            "--use-codex-planner",
            help="Use Codex planner with deterministic validation.",
        ),
    ] = False,
    disable_codex_planner: Annotated[
        bool,
        typer.Option("--disable-codex-planner", help="Force deterministic local planning."),
    ] = False,
    strict_guardrails: Annotated[
        bool,
        typer.Option("--strict-guardrails", help="Reject unsafe plan content strictly."),
    ] = False,
) -> None:
    """Create design_plan.json from existing run artifacts."""
    del strict_guardrails
    output = output_dir or run_dir
    try:
        artifacts = _design_artifacts(run_dir)
        plan = _build_design_plan(
            artifacts,
            run_dir=run_dir,
            use_codex=bool(use_codex_planner and not disable_codex_planner),
        )
    except (DesignPlanValidationError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = output / "design_plan.json"
    _write_json_model(path, plan)
    typer.echo(str(path))


@design_app.command("generate")
def design_generate_command(
    run_dir: Annotated[
        Path,
        typer.Option("--run-dir", exists=True, file_okay=False, dir_okay=True, readable=True),
    ] = Path("."),
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, dir_okay=True),
    ] = None,
    generator: Annotated[
        list[str] | None,
        typer.Option("--generator", help="Generator to enable; may be repeated."),
    ] = None,
    budget: Annotated[
        int,
        typer.Option("--budget", min=0, help="Total generated molecules per objective."),
    ] = 8,
    random_seed: Annotated[
        int | None,
        typer.Option("--random-seed", help="Deterministic generation seed."),
    ] = None,
    max_retained: Annotated[
        int,
        typer.Option("--max-retained", min=1, help="Maximum retained generated molecules."),
    ] = 50,
    strict_guardrails: Annotated[
        bool,
        typer.Option("--strict-guardrails", help="Reject invalid generated molecules."),
    ] = False,
) -> None:
    """Run the generator ensemble from design_plan.json."""
    output = output_dir or run_dir
    try:
        artifacts = _design_artifacts(run_dir)
        plan = _load_design_plan(run_dir)
        generation_run = _design_generate_from_plan(
            plan=plan,
            artifacts=artifacts,
            enabled_generators=generator or [],
            budget=budget,
            random_seed=random_seed,
            max_retained=max_retained,
            strict_guardrails=strict_guardrails,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = output / "generated_candidates_v2.json"
    _write_generation_run_artifact(path, generation_run)
    typer.echo(str(path))


@design_app.command("score")
def design_score_command(
    run_dir: Annotated[
        Path,
        typer.Option("--run-dir", exists=True, file_okay=False, dir_okay=True, readable=True),
    ] = Path("."),
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, dir_okay=True),
    ] = None,
    strict_guardrails: Annotated[
        bool,
        typer.Option("--strict-guardrails", help="Keep strict score guardrail metadata."),
    ] = False,
) -> None:
    """Run oracle scoring and write oracle_scores.json."""
    del strict_guardrails
    output = output_dir or run_dir
    try:
        run = _load_design_generation_run(run_dir)
        scored = _score_design_generation_run(run)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = output / "oracle_scores.json"
    _write_json(path, _oracle_scores_artifact(scored))
    _write_generation_run_artifact(output / "generated_candidates_v2.json", scored)
    typer.echo(str(path))


@design_app.command("readiness")
def design_readiness_command(
    run_dir: Annotated[
        Path,
        typer.Option("--run-dir", exists=True, file_okay=False, dir_okay=True, readable=True),
    ] = Path("."),
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, dir_okay=True),
    ] = None,
    strict_guardrails: Annotated[
        bool,
        typer.Option("--strict-guardrails", help="Keep strict readiness guardrail metadata."),
    ] = False,
) -> None:
    """Compute experiment-readiness buckets and write experiment_readiness.json."""
    del strict_guardrails
    output = output_dir or run_dir
    try:
        run = _load_design_generation_run(run_dir)
        ready_run, candidates = _readiness_for_generation_run(run)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    path = output / "experiment_readiness.json"
    _write_json(
        path,
        {
            "candidate_count": len(candidates),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "human_review_required": True,
            "no_lab_protocols": True,
        },
    )
    _write_generation_run_artifact(output / "generated_candidates_v2.json", ready_run)
    typer.echo(str(path))


@design_app.command("loop")
def design_loop_command(
    run_dir: Annotated[
        Path,
        typer.Option("--run-dir", exists=True, file_okay=False, dir_okay=True, readable=True),
    ] = Path("."),
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, dir_okay=True),
    ] = None,
    use_codex_planner: Annotated[
        bool,
        typer.Option(
            "--use-codex-planner",
            help="Use Codex planner with deterministic validation.",
        ),
    ] = False,
    disable_codex_planner: Annotated[
        bool,
        typer.Option("--disable-codex-planner", help="Force deterministic local planning."),
    ] = False,
    generator: Annotated[
        list[str] | None,
        typer.Option("--generator", help="Generator to enable; may be repeated."),
    ] = None,
    budget: Annotated[int, typer.Option("--budget", min=0)] = 8,
    random_seed: Annotated[int | None, typer.Option("--random-seed")] = None,
    max_retained: Annotated[int, typer.Option("--max-retained", min=1)] = 50,
    strict_guardrails: Annotated[bool, typer.Option("--strict-guardrails")] = False,
) -> None:
    """Run plan -> generate -> score -> readiness and write design_loop_report.md."""
    output = output_dir or run_dir
    try:
        artifacts = _design_artifacts(run_dir)
        plan = _build_design_plan(
            artifacts,
            run_dir=run_dir,
            use_codex=bool(use_codex_planner and not disable_codex_planner),
        )
        generated = _design_generate_from_plan(
            plan=plan,
            artifacts=artifacts,
            enabled_generators=generator or [],
            budget=budget,
            random_seed=random_seed,
            max_retained=max_retained,
            strict_guardrails=strict_guardrails,
        )
        scored = _score_design_generation_run(generated)
        ready_run, candidates = _readiness_for_generation_run(scored)
        benchmark = DesignBenchmarkHarness(random_seed=random_seed or 13).benchmark_artifact(
            _generation_run_artifact_payload(ready_run),
            output_dir=output,
        )
    except (DesignPlanValidationError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _write_json_model(output / "design_plan.json", plan)
    _write_generation_run_artifact(output / "generated_candidates_v2.json", ready_run)
    _write_json(output / "oracle_scores.json", _oracle_scores_artifact(scored))
    _write_json(
        output / "experiment_readiness.json",
        {"candidates": [candidate.model_dump(mode="json") for candidate in candidates]},
    )
    report_path = output / "design_loop_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_design_loop_markdown(plan, ready_run, candidates, benchmark))
    typer.echo(str(report_path))


@design_app.command("benchmark")
def design_benchmark_command(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to generated_candidates_v2.json or generated_candidates.json.",
        ),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", file_okay=False, dir_okay=True),
    ] = None,
    random_seed: Annotated[int, typer.Option("--random-seed")] = 13,
    strict_guardrails: Annotated[bool, typer.Option("--strict-guardrails")] = False,
) -> None:
    """Benchmark generated design artifacts."""
    del strict_guardrails
    try:
        payload = json.loads(input_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("Benchmark input must be a JSON object.")
        report = DesignBenchmarkHarness(random_seed=random_seed).benchmark_artifact(
            payload,
            output_dir=output_dir,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Design benchmark summary")
    typer.echo(f"Validity rate: {report.metrics.validity_rate:.3f}")
    typer.echo(f"Novelty rate: {report.metrics.novelty_rate:.3f}")
    typer.echo(f"Scaffold diversity: {report.metrics.scaffold_diversity:.3f}")
    typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("benchmark-developability")
def benchmark_developability(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to developability.json.",
        ),
    ],
    enable_tdc_benchmark: Annotated[
        bool,
        typer.Option(
            "--enable-tdc-benchmark",
            help="Enable optional TDC ADMET benchmark checks when tdc is installed.",
        ),
    ] = False,
    tdc_data_dir: Annotated[
        Path,
        typer.Option(
            "--tdc-data-dir",
            file_okay=False,
            dir_okay=True,
            help="Directory for optional TDC datasets when benchmark mode is enabled.",
        ),
    ] = Path(".cache/molecule-ranker/tdc"),
) -> None:
    """Benchmark V0.4 developability artifact coverage and calibration signals."""
    try:
        result = benchmark_developability_file(
            input_path,
            enable_tdc_benchmark=enable_tdc_benchmark,
            tdc_data_dir=tdc_data_dir,
        )
    except DevelopabilityBenchmarkError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Developability benchmark summary")
    typer.echo(f"Input: {input_path}")
    typer.echo(f"Assessments: {result.assessment_count}")
    typer.echo(f"Descriptor coverage: {result.descriptor_coverage:.3f}")
    typer.echo(f"Alert rate: {result.alert_rate:.3f}")
    typer.echo(f"Critical alert rate: {result.critical_alert_rate:.3f}")
    typer.echo(f"High-risk ADMET rate: {result.high_risk_admet_rate:.3f}")
    typer.echo(
        "Generated retention after developability: "
        f"{result.generated_retention_rate_after_developability:.3f}"
    )
    typer.echo(f"Risk levels: {result.risk_level_distribution}")
    typer.echo(f"Endpoint coverage: {result.endpoint_coverage}")
    if result.tdc_benchmark_enabled:
        typer.echo(f"TDC benchmark available: {result.tdc_benchmark_available}")
    typer.echo("")
    typer.echo("JSON summary:")
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("assess-developability")
def assess_developability(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to generated_candidates.json or candidates.json.",
        ),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Path for developability.json. Defaults next to the input artifact.",
        ),
    ] = None,
    enable_developability: Annotated[
        bool,
        typer.Option(
            "--enable-developability/--disable-developability",
            help="Enable or skip V0.4 developability triage.",
        ),
    ] = True,
    strict_developability: Annotated[
        bool,
        typer.Option(
            "--strict-developability/--no-strict-developability",
            help="Fail the command when developability assessment fails for a molecule.",
        ),
    ] = False,
    developability_filter_mode: Annotated[
        str,
        typer.Option(
            "--developability-filter-mode",
            help="Developability action mode for generated molecules.",
        ),
    ] = "filter_generated_only",
    reject_critical_alerts: Annotated[
        bool,
        typer.Option(
            "--reject-critical-alerts/--no-reject-critical-alerts",
            help="Reject generated molecules with critical alerts when filtering applies.",
        ),
    ] = True,
    reject_high_toxicity_risk: Annotated[
        bool,
        typer.Option(
            "--reject-high-toxicity-risk",
            help="Reject molecules with high toxicity-risk flags when filtering applies.",
        ),
    ] = False,
    enable_local_admet_models: Annotated[
        bool,
        typer.Option(
            "--enable-local-admet-models",
            help="Enable configured local ADMET model adapters when available.",
        ),
    ] = False,
    disable_rule_based_admet: Annotated[
        bool,
        typer.Option(
            "--disable-rule-based-admet",
            help="Disable the rule-based ADMET baseline triage.",
        ),
    ] = False,
    enable_structure_retrieval: Annotated[
        bool,
        typer.Option(
            "--enable-structure-retrieval",
            help="Enable optional target structure metadata retrieval.",
        ),
    ] = False,
    enable_docking: Annotated[
        bool,
        typer.Option(
            "--enable-docking",
            help=(
                "Enable optional docking plugin path when explicit structure inputs are "
                "available."
            ),
        ),
    ] = False,
    strict_structure_mode: Annotated[
        bool,
        typer.Option(
            "--strict-structure-mode",
            help="Fail optional structure/docking steps instead of warning when unavailable.",
        ),
    ] = False,
    max_structures_per_target: Annotated[
        int,
        typer.Option(
            "--max-structures-per-target",
            min=1,
            help="Maximum target structures considered per target.",
        ),
    ] = 5,
    max_docked_molecules: Annotated[
        int,
        typer.Option(
            "--max-docked-molecules",
            min=0,
            help="Maximum molecules sent to optional docking when docking is enabled.",
        ),
    ] = 20,
) -> None:
    """Run developability triage from saved candidate artifacts only."""
    config = RankerConfig(
        enable_developability=enable_developability,
        strict_developability=strict_developability,
        developability_filter_mode=developability_filter_mode,
        reject_critical_alerts=reject_critical_alerts,
        reject_high_toxicity_risk=reject_high_toxicity_risk,
        enable_local_admet_models=enable_local_admet_models,
        enable_rule_based_admet=not disable_rule_based_admet,
        enable_structure_retrieval=enable_structure_retrieval,
        enable_docking=enable_docking,
        strict_structure_mode=strict_structure_mode,
        max_structures_per_target=max_structures_per_target,
        max_docked_molecules=max_docked_molecules,
    )
    output = output_path or input_path.parent / "developability.json"
    try:
        payload = _assess_developability_artifact(input_path, output, config)
    except (OSError, ValueError, AgentExecutionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Developability assessment summary")
    typer.echo(f"Input: {input_path}")
    typer.echo(f"Assessed existing molecules: {payload['assessed_existing_count']}")
    typer.echo(f"Assessed generated molecules: {payload['assessed_generated_count']}")
    typer.echo(f"Rejected molecules: {payload['rejected_count']}")
    typer.echo(f"Output: {output}")


def _assess_developability_artifact(
    input_path: Path,
    output_path: Path,
    config: RankerConfig,
) -> dict[str, Any]:
    payload = json.loads(input_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Input artifact must contain a JSON object.")

    context = _context_from_candidate_artifact(payload, config)
    if not context.candidates and "generation_run" not in context.config:
        raise ValueError("Input artifact did not contain existing or generated molecules.")

    context = DevelopabilityAssessmentAgent().run(context)
    run = context.config.get("developability_run")
    run_payload = run.model_dump(mode="json") if isinstance(run, BaseModel) else run
    if not isinstance(run_payload, dict):
        raise ValueError("Developability assessment did not produce a run payload.")

    output_payload = {
        "success": bool(run_payload.get("enabled", False)),
        "input": str(input_path),
        "disease": context.disease.model_dump(mode="json") if context.disease else None,
        "enabled": run_payload.get("enabled", False),
        "assessed_existing_count": run_payload.get("assessed_existing_count", 0),
        "assessed_generated_count": run_payload.get("assessed_generated_count", 0),
        "retained_count": run_payload.get("retained_count", 0),
        "deprioritized_count": run_payload.get("deprioritized_count", 0),
        "rejected_count": run_payload.get("rejected_count", 0),
        "risk_distribution": _risk_distribution(run_payload),
        "alert_distribution": run_payload.get("metadata", {}).get("alert_counts", {}),
        "admet_endpoint_coverage": _admet_endpoint_coverage(run_payload),
        "assessments": run_payload.get("assessments", []),
        "warnings": run_payload.get("warnings", []),
        "limitations": [
            "Developability scores are computational triage heuristics.",
            "They do not establish safety, efficacy, or synthesizability.",
            (
                "They require medicinal chemistry, toxicology, pharmacology, and synthesis "
                "expert review."
            ),
            "No synthesis instructions are provided.",
            "No synthesis routes, protocols, reagents, or procedures are provided.",
            "No patient-specific clinical recommendations are provided.",
        ],
        "config": _standalone_developability_config(config),
        "generated_at": datetime.now(UTC).isoformat(),
        "developability_run": run_payload,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, indent=2, sort_keys=True) + "\n")
    return output_payload


def _context_from_candidate_artifact(
    payload: dict[str, Any],
    config: RankerConfig,
) -> PipelineContext:
    runtime_config = config.runtime_agent_config(
        top=config.default_top,
        results_dir=config.results_dir,
    )
    disease = _parse_optional_model(payload.get("disease"), Disease)
    candidates = [
        candidate
        for raw in payload.get("candidates", [])
        if (candidate := _parse_optional_model(raw, MoleculeCandidate)) is not None
    ]
    generated_hypotheses = [
        hypothesis
        for raw in payload.get("generated_molecule_hypotheses", [])
        if (hypothesis := _parse_optional_model(raw, GeneratedMoleculeHypothesis)) is not None
    ]
    generation_run = _generation_run_from_artifact(payload)
    if generation_run is not None:
        runtime_config["generation_run"] = generation_run
        runtime_config["enable_generation"] = True
        runtime_config["enable_novel_generation"] = True
    return PipelineContext(
        disease_input=(disease.input_name if disease is not None else "artifact"),
        disease=disease,
        candidates=candidates,
        generated_candidates=generated_hypotheses,
        config=runtime_config,
    )


def _load_experimental_candidates(
    input_path: Path,
) -> tuple[list[MoleculeCandidate], list[GeneratedMoleculeHypothesis]]:
    payload = json.loads(input_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Candidate artifact must contain a JSON object.")
    candidates = [
        candidate
        for raw in payload.get("candidates", [])
        if (candidate := _parse_optional_model(raw, MoleculeCandidate)) is not None
    ]
    generated = [
        hypothesis
        for raw in payload.get("generated_molecule_hypotheses", [])
        if (hypothesis := _parse_optional_model(raw, GeneratedMoleculeHypothesis)) is not None
    ]
    if not candidates and not generated:
        raise ValueError("Candidate artifact did not contain candidates or generated hypotheses.")
    return candidates, generated


def _load_v06_assay_results(
    input_path: Path,
    *,
    input_format: str,
    imported_by: str | None,
) -> list[AssayResult]:
    resolved = input_format.lower()
    if resolved == "auto":
        resolved = input_path.suffix.lower().lstrip(".")
    if resolved == "csv":
        return import_assay_results_csv(input_path, imported_by=imported_by)
    if resolved == "json":
        return import_assay_results_json(input_path, imported_by=imported_by)
    raise ValueError("--format must be auto, csv, or json")


def _prepare_cli_assay_result(
    result: AssayResult,
    *,
    strict: bool,
    workspace_id: str | None,
    run_id: str | None,
    default_disease: str | None,
    default_target: str | None,
) -> AssayResult:
    context_updates: dict[str, Any] = {}
    result_updates: dict[str, Any] = {
        "workspace_id": workspace_id or result.workspace_id,
        "run_id": run_id or result.run_id,
    }
    if default_disease and not (result.disease_name or result.assay_context.disease_name):
        result_updates["disease_name"] = default_disease
        context_updates["disease_name"] = default_disease
    if default_target and not (result.target_symbol or result.assay_context.target_symbol):
        result_updates["target_symbol"] = default_target
        context_updates["target_symbol"] = default_target
    if context_updates:
        result_updates["assay_context"] = result.assay_context.model_copy(update=context_updates)
    prepared = result.model_copy(update=result_updates)
    return validate_assay_result(normalize_assay_result(prepared), strict=strict)


def _experiment_results_summary_payload(results: list[AssayResult]) -> dict[str, Any]:
    outcome_counts = Counter(result.outcome_label for result in results)
    qc_counts = Counter(result.qc_status for result in results)
    endpoint_counts = Counter(result.assay_context.endpoint.name for result in results)
    warning_count = sum(len(result.metadata.get("warnings", [])) for result in results)
    return {
        "result_count": len(results),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "qc_counts": dict(sorted(qc_counts.items())),
        "endpoint_counts": dict(sorted(endpoint_counts.items())),
        "warning_count": warning_count,
        "result_ids": [result.result_id for result in results],
    }


def _load_experiment_run_candidates(
    run_dir: Path,
    *,
    include_generated: bool,
) -> tuple[list[MoleculeCandidate], list[Any]]:
    payload = _load_review_run_artifacts(run_dir, include_generated=include_generated)
    candidates = [
        candidate
        for raw in payload.get("candidates", [])
        if (candidate := _parse_optional_model(raw, MoleculeCandidate)) is not None
    ]
    generated: list[Any] = []
    if include_generated:
        for key in ("generated_molecule_hypotheses", "retained_generated_molecules"):
            raw_items = payload.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for raw in raw_items:
                parsed = _parse_optional_model(raw, GeneratedMoleculeHypothesis)
                if parsed is not None:
                    generated.append(parsed)
                    continue
                generated_model = _parse_optional_model(raw, GeneratedMolecule)
                if generated_model is not None:
                    generated.append(generated_model)
    if not candidates and not generated:
        raise ValueError("Run artifacts did not contain candidates or generated molecules.")
    return candidates, generated


def _render_experiment_cli_report(
    results: list[AssayResult],
    candidates: list[MoleculeCandidate],
    generated: list[Any],
    from_run: Path,
) -> str:
    summary = _experiment_results_summary_payload(results)
    lines = [
        "# Experimental Result Summary",
        "",
        f"- Source run: {from_run}",
        f"- Imported result count: {summary['result_count']}",
        f"- Candidate count in run: {len(candidates)}",
        f"- Generated molecule count in run: {len(generated)}",
        f"- Outcomes: {_format_distribution(summary['outcome_counts'])}",
        f"- QC statuses: {_format_distribution(summary['qc_counts'])}",
        "",
        "Reviewer decisions remain separate from imported experimental evidence.",
        "No assay result is presented as clinical efficacy, safety, cure, or treatment proof.",
        "",
        "## Results",
    ]
    for result in results:
        lines.append(
            f"- {result.result_id}: {result.candidate_name}; "
            f"{result.assay_context.assay_name}; endpoint "
            f"{result.assay_context.endpoint.name}; outcome {result.outcome_label}; "
            f"QC {result.qc_status}"
        )
    return "\n".join(lines) + "\n"


def _risk_distribution(run_payload: dict[str, Any]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    assessments = run_payload.get("assessments", [])
    if not isinstance(assessments, list):
        return distribution
    for assessment in assessments:
        if isinstance(assessment, dict):
            risk = str(assessment.get("risk_level") or "unknown")
            distribution[risk] = distribution.get(risk, 0) + 1
    return dict(sorted(distribution.items()))


def _admet_endpoint_coverage(run_payload: dict[str, Any]) -> dict[str, int]:
    coverage: dict[str, int] = {}
    assessments = run_payload.get("assessments", [])
    if not isinstance(assessments, list):
        return coverage
    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        predictions = assessment.get("admet_predictions", [])
        if not isinstance(predictions, list):
            continue
        for prediction in predictions:
            if isinstance(prediction, dict):
                endpoint = str(prediction.get("endpoint") or "unknown")
                coverage[endpoint] = coverage.get(endpoint, 0) + 1
    return dict(sorted(coverage.items()))


def _standalone_developability_config(config: RankerConfig) -> dict[str, Any]:
    metadata = config.trace_metadata()
    return {
        key: metadata[key]
        for key in [
            "enable_developability",
            "strict_developability",
            "assess_existing_molecules",
            "assess_generated_molecules",
            "developability_filter_mode",
            "reject_critical_alerts",
            "reject_high_toxicity_risk",
            "alert_mode",
            "enable_rule_based_admet",
            "enable_local_admet_models",
            "allow_rule_based_admet_fallback",
            "enable_synthesizability",
            "enable_structure_retrieval",
            "enable_docking",
            "strict_structure_mode",
            "write_docking_artifacts",
            "max_structures_per_target",
            "max_docked_molecules",
        ]
        if key in metadata
    }


class _StaticDesignPlanProvider:
    def __init__(self, output_json: dict[str, Any]) -> None:
        self.output_json = output_json

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        now = datetime.now(UTC)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=json.dumps(self.output_json, sort_keys=True),
            output_json=self.output_json,
            stdout=json.dumps(self.output_json, sort_keys=True),
            stderr="",
            return_code=0,
            started_at=now,
            completed_at=now,
        )


def _design_artifacts(run_dir: Path) -> dict[str, Any]:
    candidates = _read_optional_json(run_dir / "candidates.json")
    generated = (
        _read_optional_json(run_dir / "generated_candidates_v2.json")
        or _read_optional_json(run_dir / "generated_candidates.json")
        or {}
    )
    merged = {**candidates, **generated}
    return {
        "candidates_payload": candidates,
        "generated_payload": generated,
        "merged_payload": merged,
        "generation_run": _generation_run_from_artifact(merged),
    }


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Artifact must be a JSON object: {path}")
    return payload


def _build_design_plan(
    artifacts: dict[str, Any],
    *,
    run_dir: Path,
    use_codex: bool,
) -> DesignPlan:
    if use_codex:
        codex_plan_path = run_dir / "codex_design_plan.json"
        provider = None
        if codex_plan_path.exists():
            payload = json.loads(codex_plan_path.read_text())
            if not isinstance(payload, dict):
                raise ValueError("codex_design_plan.json must contain a JSON object.")
            provider = _StaticDesignPlanProvider(payload)
        disease, targets, candidates = _design_validation_inputs(artifacts)
        return ScientificDesignPlannerAgent(provider=provider).build_plan(
            disease=disease,
            targets=targets,
            existing_candidates=candidates,
            literature_evidence=[],
            developability_assessments=[],
            experimental_results=[],
            review_decisions=[],
            active_learning_history=[],
            artifact_manifests=[{"path": str(run_dir / "candidates.json")}],
        )
    return _deterministic_design_plan(artifacts)


def _design_validation_inputs(
    artifacts: dict[str, Any],
) -> tuple[Disease, list[Target], list[MoleculeCandidate]]:
    payload = artifacts.get("merged_payload") if isinstance(artifacts, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    disease = _parse_optional_model(payload.get("disease"), Disease)
    if disease is None:
        disease = Disease(
            input_name="artifact",
            canonical_name=str(payload.get("disease_name") or "artifact"),
            synonyms=[],
        )
    targets = [
        target
        for raw in payload.get("targets", [])
        if (target := _parse_optional_model(raw, Target)) is not None
    ]
    generation_run = artifacts.get("generation_run")
    if isinstance(generation_run, GenerationRun):
        existing_symbols = {target.symbol for target in targets}
        for objective in generation_run.objectives:
            if objective.target_symbol not in existing_symbols:
                relevance = objective.metadata.get("target_relevance_score", 0.5)
                targets.append(
                    Target(
                        symbol=objective.target_symbol,
                        name=objective.target_name,
                        identifiers=objective.target_identifiers,
                        disease_relevance_score=float(
                            relevance
                        )
                        if isinstance(relevance, (int, float))
                        else 0.5,
                    )
                )
                existing_symbols.add(objective.target_symbol)
    candidates = [
        candidate
        for raw in payload.get("candidates", [])
        if (candidate := _parse_optional_model(raw, MoleculeCandidate)) is not None
    ]
    return disease, targets, candidates


def _deterministic_design_plan(artifacts: dict[str, Any]) -> DesignPlan:
    payload = artifacts.get("merged_payload") if isinstance(artifacts, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    disease, targets, candidates = _design_validation_inputs(artifacts)
    generation_run = artifacts.get("generation_run")
    objectives: list[dict[str, Any]] = []
    if isinstance(generation_run, GenerationRun) and generation_run.objectives:
        objectives = [
            {
                "objective_id": objective.objective_id,
                "target_symbol": objective.target_symbol,
                "objective_type": objective.objective_type,
                "constraints": dict(objective.constraints),
                "seed_molecule_ids": list(objective.seed_molecule_ids),
            }
            for objective in generation_run.objectives
        ]
    elif targets:
        objectives = [
            {
                "objective_id": f"objective-{target.symbol}",
                "target_symbol": target.symbol,
                "objective_type": "target_conditioned_analog_generation",
                "constraints": {"generated_hypothesis_only": True},
                "seed_molecule_ids": [
                    _candidate_seed_id(candidate) for candidate in candidates[:3]
                ],
            }
            for target in targets[:3]
        ]
    else:
        objectives = [
            {
                "objective_id": "objective-synthetic",
                "target_symbol": "UNSPECIFIED",
                "objective_type": "target_conditioned_analog_generation",
                "constraints": {"internal_synthetic_objective": True},
                "seed_molecule_ids": [
                    _candidate_seed_id(candidate) for candidate in candidates[:3]
                ],
            }
        ]
    return DesignPlan(
        design_plan_id=str(payload.get("design_plan_id") or "deterministic-design-plan-v1-1"),
        disease_name=disease.canonical_name,
        target_priorities=[
            {
                "target_symbol": objective["target_symbol"],
                "priority": "medium",
                "basis": "deterministic artifact-derived design planning",
            }
            for objective in objectives
        ],
        design_objectives=objectives,
        seed_strategy={
            "source": "run_artifacts",
            "candidate_seed_ids": [_candidate_seed_id(candidate) for candidate in candidates],
        },
        generator_strategy={"mode": "generator_ensemble", "no_synthesis_routes": True},
        oracle_strategy={"score_name": "experiment_worthiness_score"},
        diversity_strategy={"deduplicate": True},
        uncertainty_strategy={"use_uncertainty_for_active_learning": True},
        experiment_readiness_strategy={"human_review_required": True},
        risks=[
            {
                "risk": "generated_hypotheses_unvalidated",
                "mitigation": "Keep generated molecules separate from evidence-backed candidates.",
            }
        ],
        constraints={"no_lab_protocols": True, "no_fabricated_evidence": True},
        required_followups=[{"action": "expert medchem review"}],
        codex_task_result_id="deterministic-planner-disabled",
        metadata={"codex_planner_enabled": False, "deterministic_validation": {"approved": True}},
    )


def _candidate_seed_id(candidate: MoleculeCandidate) -> str:
    for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
        value = candidate.identifiers.get(key)
        if value:
            return str(value)
    return candidate.name


def _seed_id(seed: SeedMolecule) -> str:
    for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
        value = seed.identifiers.get(key)
        if value:
            return str(value)
    return seed.name


def _load_design_plan(run_dir: Path) -> DesignPlan:
    path = run_dir / "design_plan.json"
    if not path.exists():
        raise ValueError(f"Missing design plan: {path}")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("design_plan.json must contain a JSON object.")
    return DesignPlan.model_validate(payload)


def _design_generate_from_plan(
    *,
    plan: DesignPlan,
    artifacts: dict[str, Any],
    enabled_generators: list[str],
    budget: int,
    random_seed: int | None,
    max_retained: int,
    strict_guardrails: bool,
) -> GenerationRun:
    generation_run = artifacts.get("generation_run")
    existing_run = generation_run if isinstance(generation_run, GenerationRun) else None
    seeds = list(existing_run.seeds) if existing_run is not None else []
    if not seeds:
        seeds = _seeds_from_candidate_artifacts(artifacts)
    objectives = list(existing_run.objectives) if existing_run is not None else []
    if not objectives:
        objectives = _objectives_from_design_plan(plan, seeds)
    if not objectives or not seeds:
        return GenerationRun(
            objectives=objectives,
            seeds=seeds,
            generated=[],
            retained=[],
            rejected=[],
            warnings=["No design objectives or seeds available for generation."],
            metadata={"design_plan_id": plan.design_plan_id},
        )
    config = GenerationConfig(
        generated_per_objective=budget,
        max_retained_generated=max_retained,
        generation_random_seed=random_seed,
        enabled_generators=_normalize_generator_names(enabled_generators) or None,
        reject_basic_alerts=strict_guardrails,
    )
    result = GeneratorEnsemble().run(objectives=objectives, seeds=seeds, config=config)
    scored = GeneratedMoleculeScorer().score(
        result.generated,
        objectives=objectives,
        seeds=seeds,
        retained_generated=[],
    )
    retained = scored[:max_retained]
    retained_ids = {candidate.generated_id for candidate in retained}
    rejected = [candidate for candidate in scored if candidate.generated_id not in retained_ids]
    return GenerationRun(
        objectives=objectives,
        seeds=seeds,
        generated=scored,
        retained=retained,
        rejected=rejected,
        warnings=sorted(result.warnings),
        metadata={
            "design_plan_id": plan.design_plan_id,
            "generator_ensemble": result.metadata,
            "generator_runs": result.generator_runs,
            "failures": result.failures,
        },
    )


def _seeds_from_candidate_artifacts(artifacts: dict[str, Any]) -> list[SeedMolecule]:
    payload = artifacts.get("merged_payload") if isinstance(artifacts, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    seeds: list[SeedMolecule] = []
    for raw in payload.get("candidates", []):
        candidate = _parse_optional_model(raw, MoleculeCandidate)
        if candidate is None:
            continue
        smiles = candidate.chemical_metadata.get("canonical_smiles")
        if not isinstance(smiles, str) or not smiles:
            continue
        seeds.append(
            SeedMolecule(
                name=candidate.name,
                canonical_smiles=smiles,
                identifiers=dict(candidate.identifiers),
                known_targets=list(candidate.known_targets),
                source_candidate_name=candidate.name,
                evidence_count=len(candidate.evidence),
                best_evidence_confidence=max(
                    [item.confidence for item in candidate.evidence] or [0.5]
                ),
                target_relevance_score=0.5,
                seed_selection_reason="Selected from run artifact candidate structure.",
            )
        )
    if seeds:
        return seeds
    return [
        SeedMolecule(
            name="Synthetic design seed",
            canonical_smiles="CCO",
            identifiers={"generated": "synthetic-seed"},
            known_targets=["UNSPECIFIED"],
            source_candidate_name="Synthetic design seed",
            evidence_count=0,
            best_evidence_confidence=0.0,
            target_relevance_score=0.1,
            seed_selection_reason="Fallback seed for mocked internal design artifacts.",
        )
    ]


def _objectives_from_design_plan(
    plan: DesignPlan,
    seeds: list[SeedMolecule],
) -> list[GenerationObjective]:
    seed_ids = [_seed_id(seed) for seed in seeds]
    objectives: list[GenerationObjective] = []
    for raw in plan.design_objectives:
        if not isinstance(raw, dict):
            continue
        objective_id = str(raw.get("objective_id") or f"objective-{len(objectives) + 1}")
        target_symbol = str(raw.get("target_symbol") or "UNSPECIFIED")
        objective_type = str(raw.get("objective_type") or "target_conditioned_analog_generation")
        if objective_type not in {
            "target_conditioned_analog_generation",
            "scaffold_hopping",
            "similarity_constrained_generation",
        }:
            objective_type = "target_conditioned_analog_generation"
        raw_seed_ids = raw.get("seed_molecule_ids") or raw.get("seed_ids") or seed_ids
        selected_seed_ids = [
            str(seed_id) for seed_id in raw_seed_ids if str(seed_id) in set(seed_ids)
        ] or seed_ids
        objectives.append(
            GenerationObjective(
                objective_id=objective_id,
                disease_name=plan.disease_name,
                target_symbol=target_symbol,
                seed_molecule_names=[seed.name for seed in seeds],
                seed_molecule_ids=selected_seed_ids,
                objective_type=objective_type,  # type: ignore[arg-type]
                constraints=dict(raw.get("constraints") or {}),
                metadata={"source_design_plan_id": plan.design_plan_id},
            )
        )
    return objectives


def _normalize_generator_names(values: list[str]) -> list[str]:
    aliases = {"matched_pair": "matched_pair_transformer"}
    return [aliases.get(value, value) for value in values]


def _load_design_generation_run(run_dir: Path) -> GenerationRun:
    for name in ("generated_candidates_v2.json", "generated_candidates.json"):
        path = run_dir / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f"{name} must contain a JSON object.")
        run = _generation_run_from_artifact(payload)
        if run is not None:
            return run
    raise ValueError("No generated candidate artifact found.")


def _score_design_generation_run(run: GenerationRun) -> GenerationRun:
    retained = run.retained
    if retained and any(candidate.score_breakdown is None for candidate in retained):
        retained = GeneratedMoleculeScorer().score(
            retained,
            objectives=run.objectives,
            seeds=run.seeds,
            retained_generated=[],
        )
        run = run.model_copy(update={"retained": retained, "generated": retained + run.rejected})
    context = PipelineContext(disease_input="design", config={"generation_run": run})
    updated = OracleScoringAgent().run(context)
    scored = updated.config.get("generation_run")
    if not isinstance(scored, GenerationRun):
        raise ValueError("Oracle scoring did not produce a GenerationRun.")
    return scored


def _readiness_for_generation_run(
    run: GenerationRun,
) -> tuple[GenerationRun, list[Any]]:
    context = PipelineContext(disease_input="design", config={"generation_run": run})
    updated = ExperimentReadinessAgent().run(context)
    ready_run = updated.config.get("generation_run")
    candidates = updated.config.get("experiment_ready_candidates", [])
    if not isinstance(ready_run, GenerationRun):
        raise ValueError("Experiment readiness did not produce a GenerationRun.")
    return ready_run, list(candidates)


def _generation_run_artifact_payload(run: GenerationRun) -> dict[str, Any]:
    return {
        "generated_count": len(run.generated),
        "retained_count": len(run.retained),
        "rejected_count": len(run.rejected),
        "objectives": [objective.model_dump(mode="json") for objective in run.objectives],
        "seeds": [seed.model_dump(mode="json") for seed in run.seeds],
        "generated_molecules": [candidate.model_dump(mode="json") for candidate in run.generated],
        "retained_generated_molecules": [
            candidate.model_dump(mode="json") for candidate in run.retained
        ],
        "rejected_generated_molecules": [
            {
                "generated_molecule": candidate.model_dump(mode="json"),
                "rejection_reasons": list(candidate.validation.rejection_reasons),
            }
            for candidate in run.rejected
        ],
        "warnings": list(run.warnings),
        "metadata": dict(run.metadata),
    }


def _write_generation_run_artifact(path: Path, run: GenerationRun) -> None:
    _write_json(path, _generation_run_artifact_payload(run))


def _oracle_scores_artifact(run: GenerationRun) -> dict[str, Any]:
    return {
        "score_name": "experiment_worthiness_score",
        "candidate_count": len(run.retained),
        "oracle_scores": [
            {
                "generated_id": candidate.generated_id,
                "oracle_scoring": candidate.metadata.get("oracle_scoring", {}),
                "oracle_scores": candidate.metadata.get("oracle_scores", {}),
            }
            for candidate in run.retained
        ],
        "claim_boundary": "computational triage only; not activity or binding evidence",
    }


def _design_loop_markdown(
    plan: DesignPlan,
    run: GenerationRun,
    readiness_candidates: list[Any],
    benchmark: Any,
) -> str:
    return "\n".join(
        [
            "# Design Loop Report",
            "",
            f"- Design plan: {plan.design_plan_id}",
            f"- Generated candidates: {len(run.generated)}",
            f"- Retained candidates: {len(run.retained)}",
            f"- Readiness candidates: {len(readiness_candidates)}",
            f"- Validity rate: {benchmark.metrics.validity_rate:.3f}",
            "",
            "Generated molecules are computational hypotheses.",
            "Experiment-readiness means worth expert triage, not proven activity.",
            "No synthesis instructions or lab protocols are provided.",
        ]
    ) + "\n"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _generation_run_from_artifact(payload: dict[str, Any]) -> GenerationRun | None:
    retained = _generated_molecules_from_list(payload.get("retained_generated_molecules", []))
    rejected = _rejected_generated_molecules_from_list(
        payload.get("rejected_generated_molecules", [])
    )
    generated = _generated_molecules_from_list(payload.get("generated_molecules", []))
    if not generated:
        generated = [*retained, *rejected]
    if not retained and not rejected and not generated:
        return None
    objectives = [
        objective
        for raw in payload.get("objectives", [])
        if (objective := _parse_optional_model(raw, GenerationObjective)) is not None
    ]
    seeds = [
        seed
        for raw in payload.get("seeds", [])
        if (seed := _parse_optional_model(raw, SeedMolecule)) is not None
    ]
    return GenerationRun(
        objectives=objectives,
        seeds=seeds,
        generated=generated,
        retained=retained,
        rejected=rejected,
        warnings=list(payload.get("warnings", [])),
        metadata={"source_artifact": "generated_candidates.json"},
    )


def _generated_molecules_from_list(raw_items: Any) -> list[GeneratedMolecule]:
    if not isinstance(raw_items, list):
        return []
    return [
        molecule
        for raw in raw_items
        if (molecule := _parse_optional_model(raw, GeneratedMolecule)) is not None
    ]


def _rejected_generated_molecules_from_list(raw_items: Any) -> list[GeneratedMolecule]:
    if not isinstance(raw_items, list):
        return []
    molecules: list[GeneratedMolecule] = []
    for raw in raw_items:
        if isinstance(raw, dict) and "generated_molecule" in raw:
            raw = raw["generated_molecule"]
        molecule = _parse_optional_model(raw, GeneratedMolecule)
        if molecule is not None:
            molecules.append(molecule)
    return molecules


def _parse_optional_model(raw: Any, model: type[Any]) -> Any | None:
    if not isinstance(raw, dict):
        return None
    try:
        return model.model_validate(raw)
    except Exception:
        return None


def _normalize_decision_label(label: str) -> str:
    aliases = {
        "request_follow_up": "needs_more_data",
        "advance_to_validation": "accept_for_followup",
        "reject_for_now": "reject",
        "hold_for_more_evidence": "hold",
    }
    return aliases.get(label, label)


def _normalize_followup_type(check_type: str) -> str:
    aliases = {
        "literature_review": "rerun_with_more_literature",
        "target_review": "rerun_with_more_targets",
        "developability_review": "stricter_developability",
    }
    return aliases.get(check_type, check_type)


def _read_review_workspace(path: Path) -> ReviewWorkspace:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Review workspace must contain a JSON object.")
    return ReviewWorkspace.model_validate(payload)


def _write_review_workspace(path: Path, workspace: ReviewWorkspace) -> None:
    _write_json_model(path, workspace)


def _write_json_model(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")


def _load_review_run_artifacts(run_dir: Path, *, include_generated: bool) -> dict[str, Any]:
    candidates_path = run_dir / "candidates.json"
    if not candidates_path.exists():
        raise ValueError(f"Missing run artifact: {candidates_path}")
    payload = json.loads(candidates_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("candidates.json must contain a JSON object.")
    if include_generated:
        generated_path = run_dir / "generated_candidates.json"
        if generated_path.exists():
            generated_payload = json.loads(generated_path.read_text())
            if isinstance(generated_payload, dict):
                for key in ("generated_molecule_hypotheses", "retained_generated_molecules"):
                    if key in generated_payload and key not in payload:
                        payload[key] = generated_payload[key]
            elif (
                isinstance(generated_payload, list)
                and "generated_molecule_hypotheses" not in payload
            ):
                payload["generated_molecule_hypotheses"] = generated_payload
    else:
        payload.pop("generated_molecule_hypotheses", None)
        payload.pop("retained_generated_molecules", None)
    return payload


def _reviewer_from_cli(
    reviewer_id: str | None,
    reviewer_name: str | None,
    reviewer_role: str | None,
) -> Reviewer:
    return Reviewer(
        reviewer_id=reviewer_id or "local-reviewer",
        name=reviewer_name,
        role=reviewer_role,
    )


def _require_cli_text(value: str, option_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{option_name} is required")


def _write_review_queue_json(path: Path, workspace: ReviewWorkspace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workspace_id": workspace.workspace_id,
                "run_id": workspace.run_id,
                "disease_name": workspace.disease_name,
                "created_at": workspace.created_at.isoformat(),
                "review_items": [
                    item.model_dump(mode="json") for item in workspace.review_items
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _workspace_summary_payload(workspace: ReviewWorkspace) -> dict[str, Any]:
    return {
        "workspace_id": workspace.workspace_id,
        "run_id": workspace.run_id,
        "disease_name": workspace.disease_name,
        "created_at": workspace.created_at.isoformat(),
        "review_item_count": len(workspace.review_items),
        "priority_distribution": _distribution(
            item.priority_bucket for item in workspace.review_items
        ),
        "status_distribution": _distribution(item.review_status for item in workspace.review_items),
        "decision_count": len(workspace.decisions),
        "comment_count": len(workspace.comments),
        "followup_request_count": len(workspace.followup_requests),
        "top_pending_items": [
            {
                "review_item_id": item.review_item_id,
                "candidate_name": item.candidate_name,
                "candidate_origin": item.candidate_origin,
                "priority_bucket": item.priority_bucket,
                "score": item.score,
                "confidence": item.confidence,
            }
            for item in _top_pending_items(workspace.review_items)
        ],
    }


def _distribution(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _top_pending_items(items: list[Any], limit: int = 5) -> list[Any]:
    priority_order = {
        "high_priority": 0,
        "medium_priority": 1,
        "needs_review": 2,
        "low_priority": 3,
        "reject_suggested": 4,
    }
    pending = [item for item in items if item.review_status in {"pending", "in_review"}]
    return sorted(
        pending,
        key=lambda item: (
            priority_order.get(item.priority_bucket, 99),
            -(item.score or 0.0),
            item.candidate_name,
        ),
    )[:limit]


def _find_review_item(workspace: ReviewWorkspace, review_item_id: str) -> Any:
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return item
    raise ValueError(f"Unknown review item: {review_item_id}")


def _format_distribution(distribution: Any) -> str:
    if not isinstance(distribution, dict) or not distribution:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(distribution.items()))


def _status_for_decision(decision: str) -> str | None:
    return {
        "accept_for_followup": "accepted",
        "deprioritize": "deprioritized",
        "reject": "rejected",
        "needs_more_data": "needs_more_data",
        "escalate_to_expert": "escalated",
        "hold": "pending",
    }.get(decision)


def _render_workspace_markdown(workspace: ReviewWorkspace) -> str:
    summary = _workspace_summary_payload(workspace)
    lines = [
        f"# Review Workspace: {workspace.disease_name}",
        "",
        "Human decisions are expert triage labels, not clinical conclusions.",
        "Model-generated scores do not establish safety, efficacy, binding, or synthesizability.",
        "",
        f"- Workspace ID: `{workspace.workspace_id}`",
        f"- Run ID: `{workspace.run_id}`",
        f"- Created: `{workspace.created_at.isoformat()}`",
        f"- Review items: {len(workspace.review_items)}",
        f"- Priority distribution: {_format_distribution(summary['priority_distribution'])}",
        f"- Status distribution: {_format_distribution(summary['status_distribution'])}",
        "",
        "## Review Items",
        "",
    ]
    for item in workspace.review_items:
        lines.extend(
            [
                f"### {item.candidate_name}",
                "",
                f"- Review item ID: `{item.review_item_id}`",
                f"- Origin: {item.candidate_origin}",
                f"- Priority: {item.priority_bucket}",
                f"- Status: {item.review_status}",
                f"- Score: {item.score}",
                f"- Confidence: {item.confidence}",
                f"- Targets: {', '.join(item.target_symbols) or 'n/a'}",
                "",
            ]
        )
        if item.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in item.warnings)
            lines.append("")
    if workspace.decisions:
        lines.extend(["## Reviewer Decisions", ""])
        for decision in workspace.decisions:
            lines.extend(
                [
                    f"- `{decision.created_at.isoformat()}` "
                    f"{decision.reviewer.reviewer_id}: {decision.decision} "
                    f"on `{decision.review_item_id}`",
                    f"  Rationale: {decision.rationale}",
                ]
            )
        lines.append("")
    if workspace.comments:
        lines.extend(["## Reviewer Comments", ""])
        for comment in workspace.comments:
            lines.append(
                f"- `{comment.created_at.isoformat()}` {comment.reviewer.reviewer_id} "
                f"({comment.comment_type}) on `{comment.review_item_id}`: "
                f"{comment.comment_text}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _select_project_runs(runs: list[Any], run_ids: list[str]) -> list[Any]:
    if not run_ids:
        selected = runs
    else:
        requested = set(run_ids)
        selected = [run for run in runs if run.run_id in requested]
        missing = requested - {run.run_id for run in selected}
        if missing:
            raise ValueError(f"Unknown run IDs: {', '.join(sorted(missing))}")
    if len(selected) < 2:
        raise ValueError("At least two registered runs are required.")
    return selected


def _codex_status_check(command_parts: list[str], resolved: str | None) -> dict[str, Any]:
    if resolved is None:
        return {"status": "unavailable", "stdout": "", "stderr": "Codex CLI was not found."}
    check_command = [resolved, *command_parts[1:], "--version"]
    try:
        completed = subprocess.run(
            check_command,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            env={"PATH": os.environ.get("PATH", "")},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "stdout": "", "stderr": "Version check timed out."}
    except OSError as exc:
        return {"status": "failed", "stdout": "", "stderr": redact_secrets(str(exc))}
    status = "ok" if completed.returncode == 0 else "failed"
    return {
        "status": status,
        "return_code": completed.returncode,
        "stdout": redact_secrets(completed.stdout.strip()[:1000]),
        "stderr": redact_secrets(completed.stderr.strip()[:1000]),
    }


def _run_codex_task(
    task: CodexTask,
    *,
    dry_run: bool,
    command: str,
    allow_shell_commands: bool = False,
    allowed_commands: list[str] | None = None,
) -> CodexTaskResult:
    config = CodexBackboneConfig(
        enable_codex_backbone=True,
        codex_cli_command=command,
        codex_working_dir=Path(task.working_directory),
        codex_timeout_seconds=task.timeout_seconds,
        codex_require_json=task.require_json,
        codex_dry_run=dry_run,
        codex_allow_shell_commands=allow_shell_commands,
        codex_allowed_commands=allowed_commands or [],
        codex_store_transcripts=True,
    )
    return CodexBackboneProvider(config).run_task(task)


def _build_codex_run_task(
    run_dir: Path,
    *,
    task_type: Literal["summarize_run", "explain_ranking", "plan_followup_run"],
    candidate: str | None = None,
) -> CodexTask:
    artifacts = _codex_run_artifact_paths(run_dir)
    if not artifacts:
        raise ValueError(f"No supported run artifacts found in: {run_dir}")
    candidates_path = run_dir / "candidates.json"
    if not candidates_path.exists():
        raise ValueError(f"Missing candidates.json in run directory: {run_dir}")
    artifact_refs = [str(path.resolve()) for path in artifacts]
    prompt_payload: dict[str, Any] = {
        "run_directory": str(run_dir.resolve()),
        "artifact_refs": artifact_refs,
        "constraints": [
            "Use only these existing artifacts.",
            "Do not create or modify evidence, molecules, assay results, citations, or scores.",
            (
                "Do not claim cure, treatment, safety, efficacy, binding, activity, "
                "or synthesizability."
            ),
            "No medical advice, synthesis routes, lab protocols, dosing, or treatment guidance.",
        ],
    }
    if task_type == "summarize_run":
        prompt_payload["task"] = "Summarize this molecule-ranker run for expert review."
    elif task_type == "explain_ranking":
        if not candidate:
            raise ValueError("--candidate is required for explain-candidate.")
        prompt_payload.update(
            {
                "task": "Explain why this candidate is ranked where it is.",
                "candidate_name": candidate,
                "instructions": [
                    "Use candidate records, score breakdowns, and evidence summaries only.",
                    "Include artifact_refs in the JSON response.",
                    "List unsupported claims under not_claimed.",
                ],
            }
        )
    elif task_type == "plan_followup_run":
        prompt_payload.update(
            {
                "task": "Suggest safe molecule-ranker CLI follow-up commands.",
                "instructions": [
                    "Only propose molecule-ranker CLI commands.",
                    "Do not suggest shell pipelines, network installers, destructive commands, "
                    "lab protocols, synthesis steps, dosing, or treatment actions.",
                ],
            }
        )
    return CodexTask(
        task_id=slugify(f"{run_dir.name}-{task_type}-{candidate or 'run'}"),
        task_type=task_type,
        prompt=json.dumps(prompt_payload, indent=2, sort_keys=True),
        working_directory=str(run_dir.resolve()),
        input_artifact_paths=artifact_refs,
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=300,
        require_json=True,
        metadata={"artifact_refs": artifact_refs, "candidate": candidate},
    )


def _build_codex_compare_runs_task(run_a_dir: Path, run_b_dir: Path) -> CodexTask:
    artifacts = [*_codex_run_artifact_paths(run_a_dir), *_codex_run_artifact_paths(run_b_dir)]
    if not artifacts:
        raise ValueError("No supported run artifacts found for comparison.")
    artifact_refs = [str(path.resolve()) for path in artifacts]
    prompt = {
        "task": "Compare two molecule-ranker runs using existing artifacts only.",
        "run_a_dir": str(run_a_dir.resolve()),
        "run_b_dir": str(run_b_dir.resolve()),
        "artifact_refs": artifact_refs,
        "constraints": [
            "Use only provided artifacts.",
            "Compare workflow outputs and artifact-backed differences.",
            "Do not create or modify evidence, molecules, assay results, citations, or scores.",
            (
                "Do not claim cure, treatment, safety, efficacy, binding, activity, "
                "or synthesizability."
            ),
            "No medical advice, synthesis routes, lab protocols, dosing, or treatment guidance.",
        ],
        "output": {
            "comparison_summary": "string",
            "shared_strengths": ["artifact-backed strings"],
            "differences": ["artifact-backed strings"],
            "risks": ["risk or limitation strings"],
            "artifact_refs": ["artifact paths used"],
        },
    }
    return CodexTask(
        task_id=slugify(f"compare-runs-{run_a_dir.name}-{run_b_dir.name}"),
        task_type="compare_runs",
        prompt=json.dumps(prompt, indent=2, sort_keys=True),
        working_directory=str(Path.cwd().resolve()),
        input_artifact_paths=artifact_refs,
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=300,
        require_json=True,
        metadata={"artifact_refs": artifact_refs},
    )


def _codex_run_artifact_paths(run_dir: Path) -> list[Path]:
    return select_relevant_artifacts("inspect_artifacts", run_dir)


def _codex_cli_payload(result: CodexTaskResult, output_path: Path) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "task_type": result.task_type,
        "status": result.status,
        "output_path": str(output_path),
        "artifact_refs": list(result.artifacts_read),
        "guardrail_warnings": list(result.guardrail_warnings),
        "output_json": result.output_json,
    }


def _project_codex_config(root_dir: Path, *, mode: str) -> CodexBackboneConfig:
    normalized = mode.strip().lower()
    if normalized not in {"enabled", "dry_run", "disabled"}:
        raise ValueError("Codex mode must be one of: enabled, dry_run, disabled.")
    return CodexBackboneConfig(
        enable_codex_backbone=normalized != "disabled",
        codex_working_dir=root_dir.resolve(),
        codex_dry_run=normalized == "dry_run",
        codex_require_json=True,
        codex_store_transcripts=True,
    )


def _review_codex_config(
    db_path: Path,
    *,
    mode: str,
    codex_command: str,
) -> CodexBackboneConfig:
    normalized = mode.strip().lower()
    if normalized not in {"enabled", "dry_run", "disabled"}:
        raise ValueError("Codex mode must be one of: enabled, dry_run, disabled.")
    working_dir = db_path.resolve().parent
    return CodexBackboneConfig(
        enable_codex_backbone=normalized != "disabled",
        codex_cli_command=codex_command,
        codex_working_dir=working_dir,
        codex_dry_run=normalized == "dry_run",
        codex_require_json=True,
        codex_store_transcripts=True,
        codex_guardrails_enabled=True,
    )


def _run_review_codex_assistant(
    workspace: ReviewWorkspace,
    *,
    db_path: Path,
    codex_mode: str,
    codex_command: str,
    action: Literal["questions", "summary", "compare"],
    review_item_id: str | None = None,
    item_a: str | None = None,
    item_b: str | None = None,
) -> CodexReviewArtifact:
    config = _review_codex_config(
        db_path,
        mode=codex_mode,
        codex_command=codex_command,
    )
    provider = CodexBackboneProvider(config)
    assistant = CodexReviewAssistant(provider, working_directory=config.codex_working_dir or ".")
    if action == "questions":
        if review_item_id is None:
            raise ValueError("review_item_id is required for Codex review questions.")
        return assistant.draft_questions(workspace, review_item_id)
    if action == "summary":
        if review_item_id is None:
            raise ValueError("review_item_id is required for Codex dossier summary.")
        return assistant.summarize_dossier(workspace, review_item_id)
    if item_a is None or item_b is None:
        raise ValueError("Two review item identifiers are required for Codex comparison.")
    return assistant.compare_candidates(workspace, item_a, item_b)


def _project_summary_payload(workspace: Any) -> dict[str, Any]:
    workspace_id = (
        workspace.workspace_id if hasattr(workspace, "workspace_id") else workspace.project_id
    )
    return {
        "workspace_id": workspace_id,
        "name": getattr(workspace, "name", workspace_id),
        "run_count": len(workspace.runs),
        "artifact_count": len(workspace.artifacts),
        "codex_output_count": len(getattr(workspace, "codex_outputs", [])),
        "runs": [
            {
                "run_id": run.run_id,
                "disease_name": run.disease_name,
                "candidate_count": run.candidate_count,
                "generated_candidate_count": run.generated_candidate_count,
                "target_count": run.target_count,
                "artifact_refs": [artifact.artifact_id for artifact in run.artifacts],
            }
            for run in workspace.runs
        ],
        "artifact_refs": [artifact.artifact_id for artifact in workspace.artifacts],
    }


def _codex_request(
    *,
    task: str,
    artifacts: list[Path],
    workflow: str,
    schema: dict[str, Any],
    prompt_sections: dict[str, Any] | None = None,
) -> CodexRequest:
    artifact_refs = [
        CodexArtifact.from_path(path, artifact_id=f"artifact-{index}")
        for index, path in enumerate(artifacts, start=1)
    ]
    sections = {
        "workflow": workflow,
        "artifact_grounding": [
            "Use only registered or supplied artifacts as factual biomedical sources.",
            "If evidence is missing, say it is missing rather than filling the gap.",
            "Summaries may describe uncertainty and limitations.",
        ],
    }
    if prompt_sections:
        sections.update(prompt_sections)
    return CodexRequest(
        task=task,
        prompt_sections=sections,
        artifacts=artifact_refs,
        expected_json_schema=schema,
        output_format="json",
        metadata={"workflow": workflow},
    )


def _assistant_schema(kind: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["summary", "limitations", "follow_up_tasks"],
        "properties": {
            "summary": {"type": "string"},
            "key_points": {"type": "array"},
            "review_questions": {"type": "array"},
            "follow_up_tasks": {"type": "array"},
            "limitations": {"type": "array"},
            "workflow": {"type": "string"},
            "kind": {"type": "string"},
        },
        "metadata": {"kind": kind},
    }


def _invoke_codex_request(
    request: CodexRequest,
    *,
    mode: str,
    cwd: Path,
    timeout: float,
    audit_log: Path,
) -> Any:
    provider_mode = mode.lower()
    if provider_mode not in {"enabled", "dry_run", "disabled"}:
        raise typer.BadParameter("--mode must be enabled, dry_run, or disabled")
    provider = CodexCLIProvider(
        CodexProviderConfig(
            mode=provider_mode,  # type: ignore[arg-type]
            timeout_seconds=timeout,
            working_dir=str(cwd),
            audit_log_path=str(audit_log),
        )
    )
    try:
        return provider.invoke(request)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _print_codex_response(response: Any, *, json_output: bool) -> None:
    payload = response.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        if response.status in {"error", "guardrail_violation"}:
            raise typer.Exit(code=1)
        return
    typer.echo(f"Codex status: {response.status}")
    if response.parsed_json is not None:
        typer.echo(json.dumps(response.parsed_json, indent=2, sort_keys=True))
    elif response.stdout:
        typer.echo(response.stdout)
    if response.stderr:
        typer.echo(response.stderr, err=True)
    if response.guardrail_violations:
        typer.echo("Guardrail violations:", err=True)
        for violation in response.guardrail_violations:
            typer.echo(f"- {violation.rule}: {violation.text_excerpt}", err=True)
    typer.echo(f"Audit log: {response.audit_log_path}")
    if response.status in {"error", "guardrail_violation"}:
        raise typer.Exit(code=1)


def _run_engineering_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    started = datetime.now(UTC)
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    duration = (datetime.now(UTC) - started).total_seconds()
    return {
        "command": command,
        "returncode": completed.returncode,
        "duration_seconds": duration,
        "stdout_excerpt": completed.stdout[-4000:],
        "stderr_excerpt": completed.stderr[-4000:],
    }


def _echo_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _serve_api(
    *,
    root_dir: Path,
    host: str,
    port: int,
    api_key: str | None,
    hosted_mode: bool,
    auth_secret: str | None,
    platform_db_path: Path | None,
    platform_database_url: str | None,
    enable_codex_backbone: bool,
    allow_public_bind: bool,
) -> None:
    typer.echo(f"Serving molecule-ranker API at http://{host}:{port}")
    if hosted_mode:
        typer.echo("Hosted endpoints: /auth/login, /dashboard, /ops/health, /projects")
    else:
        typer.echo("Local endpoints: /health, /projects, /projects/{id}/artifacts")
    run_local_server(
        root_dir=root_dir,
        host=host,
        port=port,
        enable_codex_backbone=enable_codex_backbone,
        api_key=api_key,
        hosted_mode=hosted_mode,
        auth_secret=auth_secret,
        platform_database_url=platform_database_url,
        platform_db_path=platform_db_path,
        allow_public_bind=allow_public_bind,
    )


def _platform_db_action(
    *,
    root_dir: Path,
    database_url: str | None,
    db_path: Path | None,
    action: str,
) -> dict[str, Any]:
    from molecule_ranker.platform.migrations import (
        check_database,
        database_from_config,
        init_database,
        run_migrations,
    )

    try:
        database = database_from_config(
            root_dir=root_dir,
            database_url=database_url,
            db_path=db_path,
            initialize=False,
        )
        if action == "init":
            return init_database(database)
        if action == "migrate":
            run_migrations(database)
            return check_database(database)
        if action == "check":
            return check_database(database)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=1)


def _platform_database(
    *,
    root_dir: Path,
    database_url: str | None,
    db_path: Path | None,
) -> Any:
    from molecule_ranker.platform.migrations import database_from_config

    return database_from_config(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        initialize=True,
    )


def _readiness_config_from_cli(
    *,
    root_dir: Path,
    database_url: str | None = None,
    db_path: Path | None = None,
    environment: str | None = None,
    artifact_storage_path: Path | None = None,
    backup_path: Path | None = None,
    secret_key: str | None = None,
    allowed_hosts: str | None = None,
    debug: bool | None = None,
    worker_enabled: bool | None = None,
    codex_worker_enabled: bool | None = None,
    external_integrations_enabled: bool | None = None,
    external_credentials_valid: bool | None = None,
) -> Any:
    from dataclasses import replace

    from molecule_ranker.platform.readiness import ReadinessConfig

    config = ReadinessConfig.from_environment(root_dir=root_dir)
    overrides: dict[str, Any] = {}
    if database_url is not None:
        overrides["database_url"] = database_url
    if db_path is not None:
        overrides["database_path"] = db_path
    if environment is not None:
        overrides["environment"] = environment
    if artifact_storage_path is not None:
        overrides["artifact_storage_root"] = artifact_storage_path
    if backup_path is not None:
        overrides["backup_path"] = backup_path
    if secret_key is not None:
        overrides["secret_key"] = secret_key
    if allowed_hosts is not None:
        overrides["allowed_hosts"] = _parse_cli_list(allowed_hosts)
    if debug is not None:
        overrides["debug"] = debug
    if worker_enabled is not None:
        overrides["worker_enabled"] = worker_enabled
    if codex_worker_enabled is not None:
        overrides["enable_codex_worker"] = codex_worker_enabled
    if external_integrations_enabled is not None:
        overrides["external_integrations_enabled"] = external_integrations_enabled
    if external_credentials_valid is not None:
        overrides["external_credentials_valid"] = external_credentials_valid
    return replace(config, **overrides)


def _emit_readiness_report(
    report: Any,
    *,
    json_output: bool,
    output_dir: Path | None,
) -> None:
    if output_dir is not None:
        from molecule_ranker.platform.readiness import write_readiness_reports

        paths = write_readiness_reports(report, output_dir)
    else:
        paths = {}
    if json_output:
        payload = report.to_dict()
        if paths:
            payload["written_reports"] = {key: str(value) for key, value in paths.items()}
        _echo_json(payload)
    else:
        typer.echo(f"Platform readiness: {report.status.upper()}")
        for check in report.checks:
            typer.echo(f"{check.status.upper():<4} {check.check_id}: {check.message}")
        if paths:
            typer.echo(f"Reports written: {paths['json']}, {paths['markdown']}")
    if report.status == "fail":
        raise typer.Exit(code=1)


def _emit_backup_result(result: Any, *, json_output: bool) -> None:
    if json_output:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"Backup: {result.status.upper()}")
        typer.echo(f"Path: {result.path}")
        typer.echo(f"Entries: {result.manifest.get('entry_count', 0)}")
        typer.echo(f"Excluded: {len(result.excluded)}")
    if result.status == "fail":
        raise typer.Exit(code=1)


def _emit_backup_verification_result(result: Any, *, json_output: bool) -> None:
    if json_output:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"Backup verification: {result.status.upper()}")
        typer.echo(f"Entries: {result.entry_count}")
        typer.echo(f"Checked: {result.checked_entries}")
        for error in result.errors:
            typer.echo(f"- {error}")
    if result.status == "fail":
        raise typer.Exit(code=1)


def _emit_restore_result(result: Any, *, json_output: bool) -> None:
    if json_output:
        _echo_json(result.to_dict())
    else:
        typer.echo(f"Restore: {result.status.upper()}")
        typer.echo(f"Input: {result.input_path}")
        typer.echo(f"Target: {result.target_dir}")
        typer.echo(f"Dry run: {result.dry_run}")
        typer.echo(f"Entries: {result.restored_entries}")
        for error in result.errors:
            typer.echo(f"- {error}")
    if result.status == "fail":
        raise typer.Exit(code=1)


def _parse_cli_list(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _update_mapping_cli(
    mapping_id: str,
    *,
    status: str,
    root_dir: Path,
    database_url: str | None,
    db_path: Path | None,
    org_id: str,
    project_id: str | None,
) -> Any:
    from molecule_ranker.integrations.store import IntegrationStore

    database = _platform_database(root_dir=root_dir, database_url=database_url, db_path=db_path)
    store = IntegrationStore(database, org_id=org_id, project_id=project_id)
    try:
        return store.update_mapping_status(
            mapping_id,
            status=status,
            metadata={"reviewed_via": "cli", "reviewed_at": datetime.now(UTC).isoformat()},
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _print_human_summary(result: RankingRun, output_dir: Path, *, verbose: bool) -> None:
    artifact_dir = output_dir / slugify(result.disease.canonical_name)
    generation = _generation_summary_from_traces(result)
    typer.echo(f"Disease: {result.disease.canonical_name}")
    typer.echo(f"Targets found: {len(result.targets)}")
    typer.echo(f"Candidates ranked: {len(result.candidates)}")
    typer.echo(f"Generated hypotheses: {len(result.generated_candidates)}")
    typer.echo(f"Generated molecules attempted: {generation['attempted']}")
    typer.echo(f"Generated molecules retained: {generation['retained']}")
    typer.echo(f"Generated molecules rejected: {generation['rejected']}")
    literature = _literature_summary_from_traces(result)
    typer.echo(f"Literature papers retrieved: {literature['literature_papers_retrieved']}")
    typer.echo(f"Literature claims extracted: {literature['literature_claims_extracted']}")
    typer.echo(f"Literature warnings: {literature['literature_warnings_count']}")
    developability = _developability_summary_from_traces(result)
    typer.echo(f"Developability assessments: {developability['developability_assessment_count']}")
    typer.echo(
        f"Developability high-risk flags: {developability['developability_high_risk_count']}"
    )
    typer.echo("")
    typer.echo("Top candidates:")
    for index, candidate in enumerate(result.candidates, start=1):
        confidence = candidate.score_breakdown.confidence if candidate.score_breakdown else 0.0
        score = candidate.score or 0.0
        typer.echo(f"{index}. {candidate.name} - score {score:.2f}, confidence {confidence:.2f}")
    typer.echo("")
    typer.echo("Files written:")
    typer.echo(str(artifact_dir / "report.md"))
    typer.echo(str(artifact_dir / "candidates.json"))
    typer.echo(str(artifact_dir / "generated_candidates.json"))
    typer.echo(str(artifact_dir / "developability_report.md"))
    typer.echo(str(artifact_dir / "developability_assessments.json"))
    typer.echo(str(artifact_dir / "developability.json"))
    typer.echo(str(artifact_dir / "trace.json"))
    if verbose:
        typer.echo("")
        typer.echo("Agent trace:")
        for trace in result.traces:
            typer.echo(f"- {trace.agent_name}: {trace.output_summary}")
            for warning in trace.warnings:
                typer.echo(f"  warning: {warning}")


def _print_generation_summary(result: RankingRun, output_dir: Path) -> None:
    artifact_dir = output_dir / slugify(result.disease.canonical_name)
    generation = _generation_summary_from_traces(result)
    typer.echo(f"Disease: {result.disease.canonical_name}")
    typer.echo(f"Generated molecules attempted: {generation['attempted']}")
    typer.echo(f"Generated molecules retained: {generation['retained']}")
    typer.echo(f"Generated molecules rejected: {generation['rejected']}")
    typer.echo("")
    typer.echo("Generated molecule hypotheses:")
    for candidate in result.generated_candidates:
        score = candidate.generation_score or 0.0
        rank = f"{candidate.rank}." if candidate.rank is not None else "-"
        typer.echo(f"{rank} {candidate.name} - score {score:.2f}")
    typer.echo("")
    typer.echo("Files written:")
    typer.echo(str(artifact_dir / "generated_candidates.json"))
    typer.echo(str(artifact_dir / "report.md"))
    typer.echo(str(artifact_dir / "trace.json"))


def _summary_payload(result: RankingRun, output_dir: Path, *, verbose: bool) -> dict[str, object]:
    artifact_dir = output_dir / slugify(result.disease.canonical_name)
    generation = _generation_summary_from_traces(result)
    payload: dict[str, object] = {
        "disease": result.disease.canonical_name,
        "targets_found": len(result.targets),
        "candidates_ranked": len(result.candidates),
        "generated_hypotheses": len(result.generated_candidates),
        "generated_molecules_attempted": generation["attempted"],
        "generated_molecules_retained": generation["retained"],
        "generated_molecules_rejected": generation["rejected"],
        **_literature_summary_from_traces(result),
        **_developability_summary_from_traces(result),
        "top_candidates": [
            {
                "rank": index,
                "name": candidate.name,
                "score": candidate.score,
                "confidence": (
                    candidate.score_breakdown.confidence if candidate.score_breakdown else None
                ),
            }
            for index, candidate in enumerate(result.candidates, start=1)
        ],
        "output_path": str(artifact_dir),
        "files_written": {
            "report_md": str(artifact_dir / "report.md"),
            "candidates_json": str(artifact_dir / "candidates.json"),
            "generated_candidates_json": str(artifact_dir / "generated_candidates.json"),
            "developability_report_md": str(artifact_dir / "developability_report.md"),
            "developability_assessments_json": str(
                artifact_dir / "developability_assessments.json"
            ),
            "developability_json": str(artifact_dir / "developability.json"),
            "trace_json": str(artifact_dir / "trace.json"),
        },
    }
    if verbose:
        payload["agent_trace"] = [
            {
                "agent_name": trace.agent_name,
                "output_summary": trace.output_summary,
                "warnings": trace.warnings,
            }
            for trace in result.traces
        ]
    return payload


def _generation_summary_from_traces(result: RankingRun) -> dict[str, int]:
    for trace in result.traces:
        if trace.agent_name != "NovelMoleculeAgent":
            continue
        metadata = trace.metadata
        run = metadata.get("generation_run")
        if not isinstance(run, dict):
            return {
                "attempted": 0,
                "retained": len(result.generated_candidates),
                "rejected": 0,
            }
        return {
            "attempted": int(run.get("raw_generated_count", 0) or 0),
            "retained": int(run.get("retained_count", 0) or 0),
            "rejected": int(run.get("rejected_count", 0) or 0),
        }
    return {
        "attempted": 0,
        "retained": len(result.generated_candidates),
        "rejected": 0,
    }


def _literature_summary_from_traces(result: RankingRun) -> dict[str, int]:
    for trace in result.traces:
        if trace.agent_name != "LiteratureEvidenceAgent":
            continue
        metadata = trace.metadata
        return {
            "literature_papers_retrieved": int(metadata.get("papers_retrieved", 0) or 0),
            "literature_claims_extracted": int(metadata.get("claims_extracted", 0) or 0),
            "literature_warnings_count": len(metadata.get("warnings", []) or []),
        }
    return {
        "literature_papers_retrieved": 0,
        "literature_claims_extracted": 0,
        "literature_warnings_count": 0,
    }


def _developability_summary_from_traces(result: RankingRun) -> dict[str, int]:
    for trace in result.traces:
        if trace.agent_name != "DevelopabilityAssessmentAgent":
            continue
        metadata = trace.metadata
        return {
            "developability_assessment_count": int(metadata.get("assessment_count", 0) or 0),
            "developability_high_risk_count": int(metadata.get("high_risk_count", 0) or 0),
            "developability_review_flag_count": int(metadata.get("review_flag_count", 0) or 0),
            "developability_insufficient_structure_count": int(
                metadata.get("insufficient_structure_count", 0) or 0
            ),
        }
    return {
        "developability_assessment_count": 0,
        "developability_high_risk_count": 0,
        "developability_review_flag_count": 0,
        "developability_insufficient_structure_count": 0,
    }


if __name__ == "__main__":
    app()
