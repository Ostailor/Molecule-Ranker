from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.evaluation.reproducibility import (
    check_reproducibility,
    load_reproducibility_manifest,
)


def _write_run(path: Path, *, seed: int | None = 13, config_value: str = "alpha") -> None:
    path.mkdir(parents=True, exist_ok=True)
    config: dict[str, object] = {"strategy": config_value}
    if seed is not None:
        config["seed"] = seed
    (path / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    (path / "candidates.json").write_text(
        json.dumps(
            {
                "input_artifact_paths": ["source_inputs/assay_fixture.json"],
                "candidates": [{"candidate_id": "C1", "score": 0.8}],
            }
        ),
        encoding="utf-8",
    )
    source_dir = path / "source_inputs"
    source_dir.mkdir()
    (source_dir / "assay_fixture.json").write_text('{"assay_results":[]}', encoding="utf-8")
    (path / "uv.lock").write_text("locked-dependency-set", encoding="utf-8")
    (path / "model_artifact.json").write_text('{"model_id":"m1"}', encoding="utf-8")
    (path / "codex_transcript.json").write_text('{"events":[]}', encoding="utf-8")
    (path / "integration_payload.json").write_text('{"system":"fixture"}', encoding="utf-8")


def _config_hash(config: dict[str, object]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_deterministic_rerun_passes_with_same_seed(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    rerun_dir = tmp_path / "rerun"
    _write_run(run_dir, seed=42)
    _write_run(rerun_dir, seed=42)

    report = check_reproducibility(from_run=run_dir, rerun_dir=rerun_dir)
    manifest = load_reproducibility_manifest(run_dir)

    assert report.status == "pass"
    assert report.metrics["deterministic_rerun_match"] is True
    assert manifest.random_seeds == {"seed": 42}
    assert "candidates.json" in manifest.input_artifact_hashes
    assert (run_dir / "reproducibility_manifest.json").exists()
    assert (run_dir / "reproducibility_report.md").exists()


def test_changed_artifact_hash_detected(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir)
    first = check_reproducibility(from_run=run_dir)
    (run_dir / "candidates.json").write_text('{"candidates":[]}', encoding="utf-8")

    second = check_reproducibility(from_run=run_dir)

    assert first.status == "pass"
    assert second.status == "fail"
    assert second.metrics["artifact_hash_match"] is False
    assert "artifact_hash_mismatch:candidates.json" in second.warnings


def test_missing_seed_warning(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, seed=None)

    report = check_reproducibility(from_run=run_dir)

    assert report.metrics["seed_reproducibility"] is False
    assert "missing_random_seed" in report.warnings


def test_config_mismatch_detected(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, config_value="beta")

    report = check_reproducibility(
        from_run=run_dir,
        expected_config_hash=_config_hash({"strategy": "alpha", "seed": 13}),
    )

    assert report.status == "fail"
    assert report.metrics["config_hash_match"] is False
    assert "config_hash_mismatch" in report.warnings


def test_reproducibility_cli_writes_manifest_and_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "glioma"
    _write_run(run_dir, seed=7)

    result = CliRunner().invoke(
        app,
        ["eval", "reproducibility", "--from-run", str(run_dir), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report"]["status"] == "pass"
    assert payload["manifest"]["random_seeds"] == {"seed": 7}
    assert (run_dir / "reproducibility_manifest.json").exists()
    assert (run_dir / "reproducibility_report.md").exists()
