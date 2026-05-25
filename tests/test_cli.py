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
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
)


class FakeOrchestrator:
    last_runtime_config: dict[str, int] | None = None

    def __init__(self, *, config, **kwargs):
        self.config = config
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


def test_help_commands_work():
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    rank = runner.invoke(app, ["rank", "--help"])

    assert root.exit_code == 0
    assert rank.exit_code == 0
    command_group = get_command(app)
    assert isinstance(command_group, click.Group)
    command = command_group.commands["rank"]
    options = {
        option
        for parameter in command.params
        for option in getattr(parameter, "opts", [])
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
        "--max-targets",
        "--max-molecules-per-target",
    } <= options


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
        ],
    )

    assert result.exit_code == 0
    output_dir = tmp_path / "alzheimer-disease"
    assert "Disease: Alzheimer disease" in result.stdout
    assert "Targets found: 0" in result.stdout
    assert "Candidates ranked: 1" in result.stdout
    assert "1. Candidate - score 0.58, confidence 0.54" in result.stdout
    assert str(output_dir / "report.md") in result.stdout
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()
    assert FakeOrchestrator.last_runtime_config == {
        "target_limit": 3,
        "limit_per_target": 2,
    }


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
    assert FakeOrchestrator.last_runtime_config == {}


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
