from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_codex_status_does_not_expose_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value-1234567890")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "codex",
            "status",
            "--command",
            str(tmp_path / "missing-codex"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "sk-test-secret-value" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["cli_exists"] is False
    assert payload["backbone_enabled"] is False


def test_codex_run_task_dry_run_writes_result(tmp_path: Path) -> None:
    artifact = tmp_path / "report.md"
    artifact.write_text("# Report\n")
    task_path = tmp_path / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "task_id": "dry-run-task",
                "task_type": "summarize_run",
                "prompt": "Summarize the provided artifact only.",
                "working_directory": str(tmp_path),
                "input_artifact_paths": [str(artifact)],
                "allowed_commands": [],
                "forbidden_commands": [],
                "expected_output_format": "json",
                "timeout_seconds": 30,
                "require_json": True,
                "metadata": {},
            }
        )
    )
    output_path = tmp_path / "result.json"

    result = CliRunner().invoke(
        app,
        [
            "codex",
            "run-task",
            str(task_path),
            "--dry-run",
            "--output",
            str(output_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    saved = json.loads(output_path.read_text())
    assert saved["status"] == "succeeded"
    assert saved["output_json"]["dry_run"] is True


def test_codex_summarize_run_builds_valid_task(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", [("Rasagiline", 0.82)])

    result = CliRunner().invoke(
        app,
        ["codex", "summarize-run", str(run_dir), "--dry-run", "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["task_type"] == "summarize_run"
    assert payload["artifact_refs"]
    saved = json.loads((run_dir / "codex_summary.json").read_text())
    assert saved["task_type"] == "summarize_run"
    assert saved["artifacts_read"]


def test_codex_explain_candidate_uses_artifact_refs(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", [("Rasagiline", 0.82)])

    result = CliRunner().invoke(
        app,
        [
            "codex",
            "explain-candidate",
            str(run_dir),
            "--candidate",
            "Rasagiline",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["task_type"] == "explain_ranking"
    assert payload["artifact_refs"]
    saved = json.loads((run_dir / "codex_explain_rasagiline.json").read_text())
    assert "Rasagiline" in saved["output_json"]["prompt"]


def test_codex_plan_followup_dry_run_has_no_unsafe_commands(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "run-a", [("Rasagiline", 0.82)])

    result = CliRunner().invoke(
        app,
        ["codex", "plan-followup", str(run_dir), "--dry-run", "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["task_type"] == "plan_followup_run"
    saved = json.loads((run_dir / "codex_followup_plan.json").read_text())
    prompt = saved["output_json"]["prompt"].lower()
    assert "molecule-ranker" in prompt
    for unsafe in ["rm -rf", "sudo", "chmod -r 777", "git push", "cat .env"]:
        assert unsafe not in prompt


def _write_run(run_dir: Path, candidates: list[tuple[str, float]]) -> Path:
    run_dir.mkdir(parents=True)
    payload = {
        "success": True,
        "disease": {"input_name": "Parkinson disease", "canonical_name": "Parkinson disease"},
        "targets": [{"symbol": "MAOB", "name": "Monoamine oxidase B"}],
        "candidates": [
            {
                "name": name,
                "origin": "existing",
                "known_targets": ["MAOB"],
                "score": score,
                "score_breakdown": {"confidence": 0.7},
            }
            for name, score in candidates
        ],
        "generated_molecule_hypotheses": [],
        "summary": {
            "target_count": 1,
            "candidate_count": len(candidates),
            "generated_candidate_count": 0,
        },
        "limitations": ["Artifact fixture for tests."],
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")
    (run_dir / "trace.json").write_text(json.dumps({"steps": []}))
    return run_dir
