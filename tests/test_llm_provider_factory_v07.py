from __future__ import annotations

import os
from pathlib import Path

from molecule_ranker.codex import (
    APILLMProvider,
    CodexCLIProvider,
    NullLLMProvider,
    create_llm_provider,
)
from molecule_ranker.codex_backbone import CodexTask


def test_provider_factory_chooses_codex_when_enabled(tmp_path: Path) -> None:
    provider = create_llm_provider(
        {
            "enable_codex_backbone": True,
            "codex_cli_command": str(tmp_path / "missing-codex"),
        }
    )

    assert isinstance(provider, CodexCLIProvider)


def test_provider_factory_chooses_null_in_tests() -> None:
    provider = create_llm_provider({"test_mode": True})

    assert isinstance(provider, NullLLMProvider)


def test_missing_codex_produces_clear_error(tmp_path: Path) -> None:
    provider = create_llm_provider(
        {
            "enable_codex_backbone": True,
            "codex_cli_command": str(tmp_path / "missing-codex"),
            "codex_working_dir": tmp_path,
        }
    )
    result = provider.run_task(_task(tmp_path))

    assert result.status == "failed"
    assert "Codex CLI unavailable" in result.stderr
    assert str(tmp_path / "missing-codex") in result.stderr


def test_api_provider_not_used_unless_configured(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value-1234567890")

    default_provider = create_llm_provider({})
    api_provider = create_llm_provider({"llm_provider": "api"})

    assert isinstance(default_provider, NullLLMProvider)
    assert not isinstance(default_provider, APILLMProvider)
    assert isinstance(api_provider, APILLMProvider)
    assert os.environ["OPENAI_API_KEY"]


def test_null_provider_returns_deterministic_task_result(tmp_path: Path) -> None:
    provider = create_llm_provider({"llm_provider": "null"})
    result = provider.run_task(_task(tmp_path))

    assert result.status == "succeeded"
    assert result.output_json is not None
    assert result.output_json["provider"] == "null"


def _task(tmp_path: Path) -> CodexTask:
    return CodexTask(
        task_id="factory-test",
        task_type="summarize_run",
        prompt="Summarize existing artifacts only.",
        working_directory=str(tmp_path),
        input_artifact_paths=[],
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=5,
        require_json=True,
        metadata={},
    )
