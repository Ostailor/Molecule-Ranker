from __future__ import annotations

import zipfile
from pathlib import Path

from molecule_ranker.codex_backbone.guardrails import redact_secrets, task_guardrail_warnings
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask
from molecule_ranker.pilot.support_bundle import create_support_bundle


def test_codex_prompt_blocks_secret_like_artifact_paths(tmp_path: Path) -> None:
    secret_path = tmp_path / ".env"
    secret_path.write_text("OPENAI_API_KEY=sk-redteamredteamredteam\n", encoding="utf-8")
    task = CodexTask(
        task_id="redteam-secret-path",
        task_type="inspect_artifacts",
        prompt="Summarize only permitted synthetic artifacts.",
        working_directory=str(tmp_path),
        input_artifact_paths=[str(secret_path)],
    )

    warnings = task_guardrail_warnings(task, CodexBackboneConfig())

    assert any("Secret-like artifact path" in warning for warning in warnings)


def test_support_bundle_excludes_codex_transcript_and_redacts_secret(tmp_path: Path) -> None:
    root = tmp_path / "bundle-root"
    (root / "logs").mkdir(parents=True)
    (root / "traces").mkdir()
    (root / "logs" / "platform.log").write_text(
        "service_token=redteam-secret-token\n",
        encoding="utf-8",
    )
    (root / "traces" / "codex_transcript.txt").write_text(
        "transcript token=redteam-secret-token\n",
        encoding="utf-8",
    )

    bundle = create_support_bundle(root_dir=root, output_path=tmp_path / "bundle.zip")

    assert redact_secrets("token=redteam-secret-token") != "token=redteam-secret-token"
    with zipfile.ZipFile(bundle.output_path) as archive:
        names = set(archive.namelist())
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in names
            if name.endswith((".json", ".log", ".txt"))
        )
    assert all("codex_transcript" not in name for name in names)
    assert "redteam-secret-token" not in combined
    assert "[REDACTED]" in combined
