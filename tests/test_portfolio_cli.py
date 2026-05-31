from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.portfolio.reports import validate_memo_guardrails
from molecule_ranker.portfolio.schemas import PortfolioCandidate


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    score: float = 0.7,
    target: str = "T1",
) -> PortfolioCandidate:
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        canonical_smiles="CCO" if origin != "generated" else "CCN",
        disease_name="Disease A",
        target_symbols=[target],
        chemical_series_id=f"series-{candidate_id}",
        scaffold_id=f"scaffold-{candidate_id}",
        evidence_score=score if origin != "generated" else 0.0,
        generation_score=score if origin == "generated" else None,
        developability_score=0.7,
        experimental_support_score=0.0,
        predictive_model_score=0.5,
        structure_score=0.5,
        experiment_readiness_score=0.7,
        uncertainty_score=0.6,
        novelty_score=0.6,
        risk_flags=[],
        blocking_risks=[],
        direct_experimental_evidence=False,
        metadata={},
    )


def _write_candidates(path: Path) -> None:
    candidates = [
        _candidate("existing-a", score=0.82, target="T1"),
        _candidate("existing-b", score=0.78, target="T2"),
        _candidate("generated-a", origin="generated", score=0.95, target="T3"),
        _candidate("generated-b", origin="generated", score=0.9, target="T4"),
    ]
    path.write_text(
        json.dumps(
            {
                "portfolio_candidates": [
                    candidate.model_dump(mode="json") for candidate in candidates
                ]
            }
        )
    )


def test_portfolio_cli_help_works() -> None:
    runner = CliRunner()
    for args in (
        ["portfolio", "--help"],
        ["portfolio", "build-candidates", "--help"],
        ["portfolio", "optimize", "--help"],
        ["portfolio", "scenarios", "--help"],
        ["portfolio", "stage-gate", "--help"],
        ["portfolio", "batch", "--help"],
        ["portfolio", "memo", "--help"],
        ["portfolio", "report", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_portfolio_cli_optimize_with_synthetic_candidates(tmp_path: Path) -> None:
    candidates_path = tmp_path / "portfolio_candidates.json"
    output = tmp_path / "portfolio_optimization.json"
    _write_candidates(candidates_path)

    result = CliRunner().invoke(
        app,
        [
            "portfolio",
            "optimize",
            "--candidates",
            str(candidates_path),
            "--algorithm",
            "greedy",
            "--max-candidates",
            "3",
            "--max-generated-fraction",
            "0.5",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["status"] == "succeeded"
    assert payload["metadata"]["deterministic_selection"] is True
    assert payload["metadata"]["input_candidates"]


def test_portfolio_cli_generated_fraction_enforced(tmp_path: Path) -> None:
    candidates_path = tmp_path / "portfolio_candidates.json"
    output = tmp_path / "portfolio_optimization.json"
    _write_candidates(candidates_path)

    result = CliRunner().invoke(
        app,
        [
            "portfolio",
            "optimize",
            "--candidates",
            str(candidates_path),
            "--max-candidates",
            "3",
            "--max-generated-fraction",
            "0.34",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    selected = payload["selections"][0]["selected_candidate_ids"]
    generated_selected = [
        candidate_id for candidate_id in selected if candidate_id.startswith("generated")
    ]
    assert len(generated_selected) <= 1


def test_portfolio_cli_memo_no_codex_fallback(tmp_path: Path) -> None:
    candidates_path = tmp_path / "portfolio_candidates.json"
    optimization = tmp_path / "portfolio_optimization.json"
    memo_path = tmp_path / "program_decision_memo.md"
    _write_candidates(candidates_path)

    optimize = CliRunner().invoke(
        app,
        [
            "portfolio",
            "optimize",
            "--candidates",
            str(candidates_path),
            "--output",
            str(optimization),
        ],
    )
    assert optimize.exit_code == 0, optimize.output

    memo = CliRunner().invoke(
        app,
        [
            "portfolio",
            "memo",
            "--optimization",
            str(optimization),
            "--output",
            str(memo_path),
            "--use-codex",
        ],
    )

    assert memo.exit_code == 0, memo.output
    text = memo_path.read_text()
    assert "## Executive summary" in text
    assert "Candidates selected and why" in text


def test_portfolio_cli_batch_has_no_protocol_text(tmp_path: Path) -> None:
    candidates_path = tmp_path / "portfolio_candidates.json"
    optimization = tmp_path / "portfolio_optimization.json"
    batch_path = tmp_path / "portfolio_batch.json"
    _write_candidates(candidates_path)

    optimize = CliRunner().invoke(
        app,
        [
            "portfolio",
            "optimize",
            "--candidates",
            str(candidates_path),
            "--output",
            str(optimization),
        ],
    )
    assert optimize.exit_code == 0, optimize.output

    batch = CliRunner().invoke(
        app,
        [
            "portfolio",
            "batch",
            "--optimization",
            str(optimization),
            "--batch-type",
            "assay_triage_batch",
            "--output",
            str(batch_path),
        ],
    )

    assert batch.exit_code == 0, batch.output
    serialized = batch_path.read_text().lower()
    assert "reagent" not in serialized
    assert "incubate" not in serialized
    assert "37 c" not in serialized
    assert "procedure" not in serialized


def test_portfolio_cli_report_uses_required_sections_and_safe_text(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    candidates = [
        _candidate("existing-a", score=0.82, target="T1"),
        _candidate("existing-b", score=0.78, target="T2"),
        _candidate("generated-a", origin="generated", score=0.95, target="T3"),
    ]
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "disease": {"canonical_name": "Disease A"},
                "candidates": [
                    {
                        "name": candidate.candidate_name,
                        "origin": candidate.origin,
                        "known_targets": candidate.target_symbols,
                        "score": candidate.evidence_score or candidate.generation_score,
                        "chemical_metadata": {
                            "canonical_smiles": candidate.canonical_smiles,
                            "chemical_series": candidate.chemical_series_id,
                            "scaffold_id": candidate.scaffold_id,
                        },
                    }
                    for candidate in candidates
                    if candidate.origin != "generated"
                ],
            }
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "portfolio",
            "report",
            "--from-run",
            str(run_dir),
            "--output",
            str(run_dir / "portfolio_report.md"),
        ],
    )

    assert result.exit_code == 0, result.output
    report = (run_dir / "portfolio_report.md").read_text()
    assert "## Portfolio Summary" in report
    assert "## Selected Candidates" in report
    assert "## Rejected and Deferred Candidates" in report
    assert "## Uncertainty and Scenario Analysis" in report
    assert validate_memo_guardrails(report) == []
