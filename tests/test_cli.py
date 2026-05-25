from __future__ import annotations

from pathlib import Path

import click
import pytest
from typer.main import get_command
from typer.testing import CliRunner

import molecule_ranker.cli as cli
from molecule_ranker.agents.base import AgentExecutionError
from molecule_ranker.cli import app
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
)


class FakeOrchestrator:
    last_runtime_config: dict[str, int] | None = None
    last_config = None

    def __init__(self, *, config, **kwargs):
        self.config = config
        FakeOrchestrator.last_config = config
        self.kwargs = kwargs

    def rank(
        self,
        disease_name: str,
        *,
        top_n: int | None = None,
        output_dir: Path | None = None,
        config: dict[str, int] | None = None,
    ):
        FakeOrchestrator.last_runtime_config = config
        artifact_dir = (output_dir or Path(self.config.results_dir)) / "alzheimer-disease"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "candidates.json").write_text("{}\n")
        (artifact_dir / "generated_candidates.json").write_text("{}\n")
        (artifact_dir / "report.md").write_text("# Report\n")
        (artifact_dir / "trace.json").write_text("{}\n")
        score_breakdown = ScoreBreakdown(
            disease_target_relevance=0.7,
            molecule_target_evidence=0.6,
            mechanism_plausibility=0.5,
            clinical_precedence=0.4,
            safety_prior=0.5,
            data_quality=0.6,
            novelty_or_repurposing_value=0.4,
            final_score=0.58,
            confidence=0.54,
            explanation="Mocked score explanation.",
        )
        return RankingRun(
            disease=Disease(
                input_name=disease_name,
                canonical_name="Alzheimer disease",
                synonyms=[],
                identifiers={"open_targets": "MONDO_TEST"},
                description=None,
            ),
            targets=[] if config is None else [],
            candidates=[
                MoleculeCandidate(
                    name="Candidate",
                    molecule_type="small_molecule",
                    identifiers={},
                    known_targets=[],
                    development_status=None,
                    mechanism_of_action=None,
                    evidence=[],
                    score=0.58,
                    score_breakdown=score_breakdown,
                    warnings=[],
                )
            ],
            traces=[
                AgentTrace(
                    agent_name="DiseaseResolverAgent",
                    input_summary="Input.",
                    output_summary="Resolved disease.",
                    warnings=[],
                    metadata={},
                )
            ],
            limitations=[],
        )


class FailingOrchestrator:
    error: Exception = DiseaseResolutionError("Disease could not be resolved.")

    def __init__(self, *, config, **kwargs):
        self.config = config

    def rank(
        self,
        disease_name: str,
        *,
        top_n: int | None = None,
        output_dir: Path | None = None,
        config: dict[str, int] | None = None,
    ):
        raise self.error


def setup_function() -> None:
    FakeOrchestrator.last_runtime_config = None
    FakeOrchestrator.last_config = None


def test_help_commands_work():
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    rank = runner.invoke(app, ["rank", "--help"])

    assert root.exit_code == 0
    assert rank.exit_code == 0
    health = runner.invoke(app, ["health", "--help"])
    assert health.exit_code == 0
    command_group = get_command(app)
    assert isinstance(command_group, click.Group)
    command = command_group.commands["rank"]
    options = {
        option
        for parameter in command.params
        for option in [
            *getattr(parameter, "opts", []),
            *getattr(parameter, "secondary_opts", []),
        ]
        if option.startswith("--")
    }
    assert "--fixture-mode" not in options
    assert "--mock-mode" not in options
    assert "--fallback" not in options
    assert {
        "--top",
        "--output-dir",
        "--json",
        "--verbose",
        "--timeout",
        "--use-cache",
        "--no-cache",
        "--cache-dir",
        "--cache-ttl-hours",
        "--max-targets",
        "--max-molecules-per-target",
        "--max-activity-records-per-target",
        "--max-indications-per-molecule",
        "--max-warnings-per-molecule",
        "--max-retries",
        "--retry-backoff-seconds",
        "--strict-enrichment",
        "--enable-literature",
        "--disable-literature",
        "--strict-literature",
        "--no-strict-literature",
        "--literature-source",
        "--openalex-enrichment",
        "--no-openalex-enrichment",
        "--max-literature-queries",
        "--max-papers-per-query",
        "--max-targets-for-literature",
        "--max-candidates-for-literature",
        "--ncbi-email",
        "--ncbi-api-key-env",
        "--enable-novel-generation",
        "--disable-novel-generation",
        "--enable-generation",
        "--disable-generation",
        "--strict-generation",
        "--no-strict-generation",
        "--generation-method",
        "--include-generated-in-main-ranking",
        "--separate-generated-ranking",
        "--generation-random-seed",
        "--max-seed-molecules",
        "--max-generation-objectives",
        "--generated-per-objective",
        "--max-retained-generated",
        "--reject-basic-alerts",
    } <= options


def test_generate_command_help_is_registered():
    runner = CliRunner()

    generate = runner.invoke(app, ["generate", "--help"])

    assert generate.exit_code == 0
    benchmark = runner.invoke(app, ["benchmark-generation", "--help"])
    assert benchmark.exit_code == 0
    command_group = get_command(app)
    assert isinstance(command_group, click.Group)
    command = command_group.commands["generate"]
    options = {
        option
        for parameter in command.params
        for option in [
            *getattr(parameter, "opts", []),
            *getattr(parameter, "secondary_opts", []),
        ]
        if option.startswith("--")
    }
    assert "--max-retained-generated" in options
    assert "--top" in options


def test_benchmark_generation_command_prints_summary(tmp_path):
    input_path = tmp_path / "generated_candidates.json"
    input_path.write_text(
        cli.json.dumps(
            {
                "success": True,
                "generation_enabled": True,
                "generated_count": 1,
                "retained_count": 1,
                "rejected_count": 0,
                "retained_generated_molecules": [
                    {
                        "generated_id": "gen-1",
                        "canonical_smiles": "CCO",
                        "conditioned_targets": ["MAOB"],
                        "validation": {"valid_rdkit_mol": True},
                        "novelty": {
                            "novelty_class": "novel_analog",
                            "max_similarity_to_seed": 0.5,
                            "max_similarity_to_existing": 0.1,
                        },
                    }
                ],
                "rejected_generated_molecules": [],
            }
        )
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["benchmark-generation", "--input", str(input_path)],
    )

    assert result.exit_code == 0
    assert "Generation benchmark summary" in result.stdout
    assert "Validity rate: 1.000" in result.stdout
    assert '"validity_rate": 1.0' in result.stdout


class HealthyAdapter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def health_check(self, *, timeout_seconds: float = 10.0) -> AdapterHealthStatus:
        return AdapterHealthStatus(
            source_name="Healthy Source",
            ok=True,
            endpoint="https://example.org/healthy",
            latency_ms=12.3,
            error=None,
            metadata={"probe": "mocked", "timeout_seconds": timeout_seconds},
        )


class FailingHealthAdapter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def health_check(self, *, timeout_seconds: float = 10.0) -> AdapterHealthStatus:
        return AdapterHealthStatus(
            source_name="Failing Source",
            ok=False,
            endpoint="https://example.org/failing",
            latency_ms=45.6,
            error="timeout",
            metadata={"timeout_seconds": timeout_seconds},
        )


class HealthyPubMedAdapter(HealthyAdapter):
    def health_check(self, *, timeout_seconds: float = 10.0) -> AdapterHealthStatus:
        status = super().health_check(timeout_seconds=timeout_seconds)
        return status.model_copy(update={"source_name": "PubMed"})


class HealthyOpenAlexAdapter(HealthyAdapter):
    def health_check(self, *, timeout_seconds: float = 10.0) -> AdapterHealthStatus:
        status = super().health_check(timeout_seconds=timeout_seconds)
        return status.model_copy(update={"source_name": "OpenAlex"})


def test_health_command_prints_adapter_statuses(monkeypatch):
    monkeypatch.setattr(cli, "OpenTargetsAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "ChEMBLAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "PubChemAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "LiteraturePubMedAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "LiteratureOpenAlexAdapter", HealthyAdapter)
    runner = CliRunner()

    result = runner.invoke(app, ["health", "--timeout", "2"])

    assert result.exit_code == 0
    assert "Source" in result.stdout
    assert "Healthy Source" in result.stdout
    assert "OK" in result.stdout
    assert "12.3 ms" in result.stdout
    assert "https://example.org/healthy" in result.stdout


def test_health_command_includes_literature_adapters(monkeypatch):
    monkeypatch.setattr(cli, "OpenTargetsAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "ChEMBLAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "PubChemAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "LiteraturePubMedAdapter", HealthyPubMedAdapter)
    monkeypatch.setattr(cli, "LiteratureOpenAlexAdapter", HealthyOpenAlexAdapter)
    runner = CliRunner()

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "PubMed" in result.stdout
    assert "OpenAlex" in result.stdout


def test_health_command_returns_nonzero_when_any_adapter_fails(monkeypatch):
    monkeypatch.setattr(cli, "OpenTargetsAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "ChEMBLAdapter", FailingHealthAdapter)
    monkeypatch.setattr(cli, "PubChemAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "LiteraturePubMedAdapter", HealthyAdapter)
    monkeypatch.setattr(cli, "LiteratureOpenAlexAdapter", HealthyAdapter)
    runner = CliRunner()

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 1
    assert "Failing Source" in result.stdout
    assert "FAIL" in result.stdout
    assert "timeout" in result.stdout


def test_rank_command_prints_success_summary_and_writes_expected_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Alzheimer disease",
            "--top",
            "1",
            "--output-dir",
            str(tmp_path),
            "--timeout",
            "5",
            "--max-targets",
            "3",
            "--max-molecules-per-target",
            "2",
            "--max-activity-records-per-target",
            "4",
            "--max-indications-per-molecule",
            "5",
            "--max-warnings-per-molecule",
            "6",
            "--max-retries",
            "7",
            "--retry-backoff-seconds",
            "0.25",
            "--strict-enrichment",
            "--disable-literature",
            "--strict-literature",
            "--literature-source",
            "pubmed",
            "--no-openalex-enrichment",
            "--max-literature-queries",
            "11",
            "--max-papers-per-query",
            "12",
            "--max-targets-for-literature",
            "13",
            "--max-candidates-for-literature",
            "14",
            "--ncbi-email",
            "ops@example.org",
            "--ncbi-api-key-env",
            "NCBI_TEST_KEY",
            "--enable-generation",
            "--strict-generation",
            "--generation-method",
            "selfies_mutation",
            "--include-generated-in-main-ranking",
            "--max-seed-molecules",
            "12",
            "--max-generation-objectives",
            "5",
            "--generated-per-objective",
            "8",
            "--max-retained-generated",
            "7",
            "--generation-random-seed",
            "123",
            "--reject-basic-alerts",
        ],
    )

    assert result.exit_code == 0
    output_dir = tmp_path / "alzheimer-disease"
    assert "Disease: Alzheimer disease" in result.stdout
    assert "Targets found: 0" in result.stdout
    assert "Candidates ranked: 1" in result.stdout
    assert "Generated hypotheses: 0" in result.stdout
    assert "Generated molecules attempted: 0" in result.stdout
    assert "Generated molecules retained: 0" in result.stdout
    assert "Generated molecules rejected: 0" in result.stdout
    assert str(output_dir / "generated_candidates.json") in result.stdout
    assert "1. Candidate - score 0.58, confidence 0.54" in result.stdout
    assert str(output_dir / "report.md") in result.stdout
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()
    assert FakeOrchestrator.last_runtime_config in ({}, None)
    assert FakeOrchestrator.last_config is not None
    assert FakeOrchestrator.last_config.default_target_limit == 3
    assert FakeOrchestrator.last_config.max_molecules_per_target == 2
    assert FakeOrchestrator.last_config.max_activity_records_per_target == 4
    assert FakeOrchestrator.last_config.max_indications_per_molecule == 5
    assert FakeOrchestrator.last_config.max_warnings_per_molecule == 6
    assert FakeOrchestrator.last_config.request_timeout_seconds == 5
    assert FakeOrchestrator.last_config.max_retries == 7
    assert FakeOrchestrator.last_config.retry_backoff_seconds == 0.25
    assert FakeOrchestrator.last_config.strict_enrichment is True
    assert FakeOrchestrator.last_config.enable_literature is False
    assert FakeOrchestrator.last_config.strict_literature is True
    assert FakeOrchestrator.last_config.literature_sources == ["pubmed"]
    assert FakeOrchestrator.last_config.enable_openalex_enrichment is False
    assert FakeOrchestrator.last_config.max_literature_queries == 11
    assert FakeOrchestrator.last_config.max_papers_per_query == 12
    assert FakeOrchestrator.last_config.max_targets_for_literature == 13
    assert FakeOrchestrator.last_config.max_candidates_for_literature == 14
    assert FakeOrchestrator.last_config.ncbi_email == "ops@example.org"
    assert FakeOrchestrator.last_config.ncbi_api_key is None
    assert FakeOrchestrator.last_config.enable_generation is True
    assert FakeOrchestrator.last_config.strict_generation is True
    assert FakeOrchestrator.last_config.generation_method == "selfies_mutation"
    assert FakeOrchestrator.last_config.include_generated_in_main_ranking is True
    assert FakeOrchestrator.last_config.max_seed_molecules == 12
    assert FakeOrchestrator.last_config.max_generation_objectives == 5
    assert FakeOrchestrator.last_config.generated_per_objective == 8
    assert FakeOrchestrator.last_config.max_retained_generated == 7
    assert FakeOrchestrator.last_config.generation_random_seed == 123
    assert FakeOrchestrator.last_config.reject_basic_alerts is True


def test_generation_command_enables_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "generate",
            "Alzheimer disease",
            "--top",
            "10",
            "--output-dir",
            str(tmp_path),
            "--max-retained-generated",
            "25",
        ],
    )

    assert result.exit_code == 0
    assert FakeOrchestrator.last_config is not None
    assert FakeOrchestrator.last_config.enable_generation is True
    assert FakeOrchestrator.last_config.max_retained_generated == 25
    assert "Generated molecules retained: 0" in result.stdout
    assert str(tmp_path / "alzheimer-disease" / "generated_candidates.json") in result.stdout


def test_rank_command_keeps_generation_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Alzheimer disease",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert FakeOrchestrator.last_config is not None
    assert FakeOrchestrator.last_config.enable_generation is False


def test_rank_command_reads_ncbi_api_key_from_named_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NCBI_TEST_KEY", "secret-key")
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Alzheimer disease",
            "--output-dir",
            str(tmp_path),
            "--ncbi-api-key-env",
            "NCBI_TEST_KEY",
        ],
    )

    assert result.exit_code == 0
    assert FakeOrchestrator.last_config is not None
    assert FakeOrchestrator.last_config.ncbi_api_key == "secret-key"


def test_rank_command_does_not_override_default_target_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Alzheimer disease",
            "--top",
            "5",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert FakeOrchestrator.last_runtime_config in ({}, None)
    config = FakeOrchestrator.last_config
    assert config is not None
    assert config.default_target_limit > 1


def test_rank_command_prints_json_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Alzheimer disease",
            "--output-dir",
            str(tmp_path),
            "--json",
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    payload = cli.json.loads(result.stdout)
    assert payload["disease"] == "Alzheimer disease"
    assert payload["candidates_ranked"] == 1
    assert payload["generated_hypotheses"] == 0
    assert payload["top_candidates"][0]["name"] == "Candidate"
    assert payload["top_candidates"][0]["confidence"] == 0.54
    assert payload["files_written"]["report_md"].endswith("report.md")
    assert payload["agent_trace"][0]["agent_name"] == "DiseaseResolverAgent"


def test_rank_command_prints_verbose_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Alzheimer disease",
            "--output-dir",
            str(tmp_path),
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    assert "Agent trace:" in result.stdout
    assert "- DiseaseResolverAgent: Resolved disease." in result.stdout


@pytest.mark.parametrize(
    ("error", "label"),
    [
        (
            DiseaseResolutionError('Could not resolve disease input: "Unknown disease"'),
            "DiseaseResolutionError",
        ),
        (TargetDiscoveryError("Target discovery failed."), "TargetDiscoveryError"),
        (MoleculeRetrievalError("Molecule retrieval failed."), "MoleculeRetrievalError"),
        (
            ExternalDataUnavailableError("Open Targets is unavailable."),
            "ExternalDataUnavailableError",
        ),
        (NoCandidatesFoundError("No candidates found."), "NoCandidatesFoundError"),
        (AgentExecutionError("Agent failed unexpectedly."), "AgentExecutionError"),
    ],
)
def test_rank_command_surfaces_pipeline_failures(tmp_path, monkeypatch, error, label):
    class ConfiguredFailingOrchestrator(FailingOrchestrator):
        pass

    ConfiguredFailingOrchestrator.error = error
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", ConfiguredFailingOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Unknown disease",
            "--top",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert f"Error: {label}" in result.stderr
    assert str(error) in result.stderr
    assert "No report was generated." in result.stderr
    assert not (tmp_path / "unknown-disease" / "report.md").exists()


def test_rank_command_prints_clear_ambiguous_disease_message(tmp_path, monkeypatch):
    class AmbiguousOrchestrator(FailingOrchestrator):
        error = DiseaseResolutionError(
            "Disease input was ambiguous. Top matches: Alpha condition (MONDO_1), "
            "Beta condition (MONDO_2)"
        )

    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", AmbiguousOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "condition",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Disease input was ambiguous. Top matches:" in result.stderr
    assert "Alpha condition" in result.stderr
    assert "Beta condition" in result.stderr
