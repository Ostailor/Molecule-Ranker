from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import molecule_ranker.cli as cli
from molecule_ranker.cli import app
from molecule_ranker.data_sources.errors import DiseaseResolutionError
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
)


class FakeOrchestrator:
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
        raise DiseaseResolutionError(f'Could not resolve disease input: "{disease_name}"')


def test_help_commands_work():
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    rank = runner.invoke(app, ["rank", "--help"])

    assert root.exit_code == 0
    assert rank.exit_code == 0
    assert "--fixture-mode" not in rank.stdout
    assert "--mock-mode" not in rank.stdout
    assert "--fallback" not in rank.stdout
    assert "--output-dir" in rank.stdout
    assert "--json" in rank.stdout
    assert "--verbose" in rank.stdout
    assert "--timeout" in rank.stdout
    assert "--max-targets" in rank.stdout
    assert "--max-molecules-per-ta" in rank.stdout


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


def test_rank_command_surfaces_pipeline_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FailingOrchestrator)
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
    assert "Error: DiseaseResolutionError" in result.stderr
    assert 'Could not resolve disease input: "Unknown disease"' in result.stderr
    assert "No report was generated." in result.stderr
    assert not (tmp_path / "unknown-disease" / "report.md").exists()
