from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from molecule_ranker import __version__
from molecule_ranker.agents.base import AgentExecutionError
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
from molecule_ranker.literature.adapters.openalex_adapter import (
    OpenAlexAdapter as LiteratureOpenAlexAdapter,
)
from molecule_ranker.literature.adapters.pubmed_adapter import (
    PubMedAdapter as LiteraturePubMedAdapter,
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
) -> None:
    """Run the V0.2 existing-molecule ranking pipeline with literature evidence."""
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
    literature = _literature_summary_from_traces(result)
    typer.echo(
        f"Literature papers retrieved: {literature['literature_papers_retrieved']}"
    )
    typer.echo(
        f"Literature claims extracted: {literature['literature_claims_extracted']}"
    )
    typer.echo(f"Literature warnings: {literature['literature_warnings_count']}")
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
        **_literature_summary_from_traces(result),
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


if __name__ == "__main__":
    app()
