from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from molecule_ranker import __version__
from molecule_ranker.agents.base import AgentExecutionError
from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources import ChEMBLAdapter, OpenTargetsAdapter, PubChemAdapter
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    EvidenceRetrievalError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator
from molecule_ranker.schemas import RankingRun
from molecule_ranker.utils import slugify

PIPELINE_ERRORS = (
    DiseaseResolutionError,
    TargetDiscoveryError,
    MoleculeRetrievalError,
    EvidenceRetrievalError,
    NoCandidatesFoundError,
    ExternalDataUnavailableError,
    AgentExecutionError,
)

app = typer.Typer(
    help="Rank existing molecules for disease research hypotheses using transparent evidence.",
    no_args_is_help=True,
    context_settings={"max_content_width": 120},
)


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
        ChEMBLAdapter(timeout_seconds=timeout, max_retries=0, retry_delay_seconds=0),
        PubChemAdapter(timeout_seconds=timeout),
    ]
    statuses = [adapter.health_check(timeout_seconds=timeout) for adapter in adapters]

    typer.echo("Source\tStatus\tLatency\tEndpoint\tError")
    for status in statuses:
        state = "OK" if status.ok else "FAIL"
        latency = f"{status.latency_ms:.1f} ms" if status.latency_ms is not None else "n/a"
        error = status.error or ""
        typer.echo(
            f"{status.source_name}\t{state}\t{latency}\t{status.endpoint}\t{error}"
        )

    if not all(status.ok for status in statuses):
        raise typer.Exit(code=1)


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
) -> None:
    """Run the V0.1 existing-molecule ranking pipeline."""
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
        max_molecules_per_target=(
            max_molecules_per_target or defaults.max_molecules_per_target
        ),
        max_activity_records_per_target=(
            max_activity_records_per_target or defaults.max_activity_records_per_target
        ),
        max_indications_per_molecule=max_indications_per_molecule,
        max_warnings_per_molecule=max_warnings_per_molecule,
        request_timeout_seconds=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        strict_enrichment=strict_enrichment,
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


def _print_human_summary(result: RankingRun, output_dir: Path, *, verbose: bool) -> None:
    artifact_dir = output_dir / slugify(result.disease.canonical_name)
    typer.echo(f"Disease: {result.disease.canonical_name}")
    typer.echo(f"Targets found: {len(result.targets)}")
    typer.echo(f"Candidates ranked: {len(result.candidates)}")
    typer.echo("")
    typer.echo("Top candidates:")
    for index, candidate in enumerate(result.candidates, start=1):
        confidence = (
            candidate.score_breakdown.confidence if candidate.score_breakdown else 0.0
        )
        score = candidate.score or 0.0
        typer.echo(
            f"{index}. {candidate.name} - score {score:.2f}, confidence {confidence:.2f}"
        )
    typer.echo("")
    typer.echo("Files written:")
    typer.echo(str(artifact_dir / "report.md"))
    typer.echo(str(artifact_dir / "candidates.json"))
    typer.echo(str(artifact_dir / "trace.json"))
    if verbose:
        typer.echo("")
        typer.echo("Agent trace:")
        for trace in result.traces:
            typer.echo(f"- {trace.agent_name}: {trace.output_summary}")
            for warning in trace.warnings:
                typer.echo(f"  warning: {warning}")


def _summary_payload(result: RankingRun, output_dir: Path, *, verbose: bool) -> dict[str, object]:
    artifact_dir = output_dir / slugify(result.disease.canonical_name)
    payload: dict[str, object] = {
        "disease": result.disease.canonical_name,
        "targets_found": len(result.targets),
        "candidates_ranked": len(result.candidates),
        "top_candidates": [
            {
                "rank": index,
                "name": candidate.name,
                "score": candidate.score,
                "confidence": (
                    candidate.score_breakdown.confidence
                    if candidate.score_breakdown
                    else None
                ),
            }
            for index, candidate in enumerate(result.candidates, start=1)
        ],
        "output_path": str(artifact_dir),
        "files_written": {
            "report_md": str(artifact_dir / "report.md"),
            "candidates_json": str(artifact_dir / "candidates.json"),
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


if __name__ == "__main__":
    app()
