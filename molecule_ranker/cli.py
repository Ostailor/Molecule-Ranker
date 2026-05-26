from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from molecule_ranker import __version__
from molecule_ranker.agents.base import AgentExecutionError, PipelineContext
from molecule_ranker.agents.developability_assessment import DevelopabilityAssessmentAgent
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
from molecule_ranker.developability.benchmark import (
    DevelopabilityBenchmarkError,
    benchmark_developability_file,
)
from molecule_ranker.generation.benchmark import (
    GenerationBenchmarkError,
    benchmark_generated_file,
)
from molecule_ranker.generation.errors import GenerationError
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationObjective,
    GenerationRun,
    SeedMolecule,
)
from molecule_ranker.literature.adapters.openalex_adapter import (
    OpenAlexAdapter as LiteratureOpenAlexAdapter,
)
from molecule_ranker.literature.adapters.pubmed_adapter import (
    PubMedAdapter as LiteraturePubMedAdapter,
)
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator
from molecule_ranker.review import (
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
)
from molecule_ranker.utils import slugify

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
app.add_typer(review_app, name="review")


@app.callback()
def main() -> None:
    """Agent-first molecule ranking research prototype."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


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
            help="Generated molecule backend to use. V0.3 supports selfies_mutation.",
        ),
    ] = "selfies_mutation",
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
) -> None:
    """Run the V0.4 ranking pipeline with developability triage."""
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
            help="Generated molecule backend to use. V0.3 supports selfies_mutation.",
        ),
    ] = "selfies_mutation",
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


def _echo_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


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
