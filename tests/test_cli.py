from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import molecule_ranker.cli as cli
from molecule_ranker.cli import app
from molecule_ranker.data_sources.errors import DiseaseResolutionError
from molecule_ranker.schemas import Disease, MoleculeCandidate, RankingRun


class FakeOrchestrator:
    def __init__(self, *, config):
        self.config = config

    def rank(
        self,
        disease_name: str,
        *,
        top_n: int | None = None,
        output_dir: Path | None = None,
    ):
        output_dir = (output_dir or Path(self.config.results_dir)) / "parkinson-disease"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "candidates.json").write_text("{}\n")
        (output_dir / "report.md").write_text("# Report\n")
        (output_dir / "trace.json").write_text("{}\n")
        return RankingRun(
            disease=Disease(
                input_name=disease_name,
                canonical_name="Parkinson disease",
                synonyms=[],
                identifiers={"open_targets": "MONDO_0005180"},
                description=None,
            ),
            targets=[],
            candidates=[
                MoleculeCandidate(
                    name="Candidate",
                    molecule_type="small_molecule",
                    identifiers={},
                    known_targets=[],
                    development_status=None,
                    mechanism_of_action=None,
                    evidence=[],
                    score=None,
                    score_breakdown=None,
                    warnings=[],
                )
            ],
            traces=[],
            limitations=[],
        )


class FailingOrchestrator:
    def __init__(self, *, config):
        self.config = config

    def rank(
        self,
        disease_name: str,
        *,
        top_n: int | None = None,
        output_dir: Path | None = None,
    ):
        raise DiseaseResolutionError("Disease could not be resolved.")


def test_rank_command_writes_expected_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MoleculeRankerOrchestrator", FakeOrchestrator)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "rank",
            "Parkinson disease",
            "--top",
            "1",
            "--results-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    output_dir = tmp_path / "parkinson-disease"
    assert "Wrote 1 candidates" in result.stdout
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()


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
            "--results-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Ranking failed: Disease could not be resolved." in result.stderr
    assert not (tmp_path / "unknown-disease" / "report.md").exists()
