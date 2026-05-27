from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.codex import (
    CodexCLIProvider,
    CodexProviderConfig,
    CodexRequest,
)


def test_codex_provider_dry_run_is_artifact_grounded_and_audited(tmp_path: Path) -> None:
    artifact = tmp_path / "report.md"
    artifact.write_text("# Report\n\nSource-backed artifact.\n")
    audit_log = tmp_path / "audit.jsonl"
    request = CodexRequest(
        task="Summarize the report limitations.",
        artifacts=[],
        expected_json_schema={"required": ["status", "artifact_count"]},
    )

    response = CodexCLIProvider(
        CodexProviderConfig(mode="dry_run", audit_log_path=str(audit_log))
    ).invoke(request)

    assert response.status == "dry_run"
    assert response.parsed_json is not None
    assert response.parsed_json["status"] == "dry_run"
    assert audit_log.exists()
    audit_record = json.loads(audit_log.read_text().splitlines()[0])
    assert audit_record["request"]["task"] == "Summarize the report limitations."


def test_codex_provider_rejects_prohibited_biomedical_claims(tmp_path: Path) -> None:
    response = CodexCLIProvider(
        CodexProviderConfig(mode="dry_run", audit_log_path=str(tmp_path / "audit.jsonl"))
    ).invoke(CodexRequest(task="Claim that Molecule X cures disease."))

    assert response.status == "guardrail_violation"
    assert response.guardrail_violations


def test_codex_provider_validates_json_output_schema(tmp_path: Path) -> None:
    script = tmp_path / "codex_stub.py"
    script.write_text("print('{\"summary\": \"ok\"}')\n")
    response = CodexCLIProvider(
        CodexProviderConfig(
            mode="enabled",
            command=[sys.executable, str(script)],
            working_dir=str(tmp_path),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
    ).invoke(
        CodexRequest(
            task="Summarize artifacts.",
            expected_json_schema={"required": ["summary"]},
        )
    )

    assert response.status == "ok"
    assert response.parsed_json == {"summary": "ok"}


def test_codex_assist_plan_cli_dry_run_outputs_json(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "codex",
            "assist",
            "plan",
            "Plan artifact inspection.",
            "--mode",
            "dry_run",
            "--audit-log",
            str(tmp_path / "audit.jsonl"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry_run"
    assert payload["parsed_json"]["status"] == "dry_run"
