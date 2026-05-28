from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from typer.main import get_command

from molecule_ranker.cli import app

DEMO_DIR = Path("examples/v1_0_demo")


def test_v1_demo_assets_are_synthetic_and_complete() -> None:
    required = {
        "README.md",
        "synthetic_assay_results.csv",
        "synthetic_external_sync_payload.json",
        "demo_commands.sh",
        "expected_artifacts_manifest.json",
    }

    assert DEMO_DIR.is_dir()
    assert required <= {path.name for path in DEMO_DIR.iterdir()}

    combined_text = "\n".join((DEMO_DIR / name).read_text() for name in sorted(required))
    assert "synthetic" in combined_text.lower()
    assert "ExampleCandidateA" in combined_text
    assert "ExampleTargetA" in combined_text
    assert "ExampleDiseaseA" in combined_text
    assert not re.search(r"\bPMID\s*:?\s*\d+", combined_text, flags=re.IGNORECASE)
    assert not re.search(r"\b10\.\d{4,9}/\S+", combined_text)
    assert "validated active" not in combined_text.lower()
    assert "real-world outcome" not in combined_text.lower()

    manifest = json.loads((DEMO_DIR / "expected_artifacts_manifest.json").read_text())
    assert manifest["synthetic"] is True
    assert {item["workflow_step"] for item in manifest["expected_artifacts"]} >= {
        "project_create",
        "mocked_offline_ranking",
        "generated_hypotheses",
        "developability",
        "review_workspace",
        "assay_import",
        "active_learning",
        "integration_dry_run",
        "codex_mocked_summary",
        "export_package",
        "dashboard_build",
    }


def test_v1_demo_commands_are_shell_and_cli_syntax_valid() -> None:
    script = DEMO_DIR / "demo_commands.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

    command_paths = _molecule_ranker_command_paths(script)
    assert command_paths
    click_root = get_command(app)
    for command_path in command_paths:
        _assert_click_command_path_exists(click_root, command_path)


def _molecule_ranker_command_paths(script: Path) -> list[list[str]]:
    paths: list[list[str]] = []
    for raw_line in script.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith("molecule-ranker "):
            continue
        tokens = shlex.split(line)
        command_path: list[str] = []
        group: Any = get_command(app)
        for token in tokens[1:]:
            commands = getattr(group, "commands", None)
            if token.startswith("-") or not isinstance(commands, dict):
                break
            if token not in commands:
                break
            command_path.append(token)
            group = commands[token]
        paths.append(command_path)
    return paths


def _assert_click_command_path_exists(root: Any, command_path: list[str]) -> None:
    command: Any = root
    for segment in command_path:
        commands = getattr(command, "commands", None)
        assert isinstance(commands, dict), f"{command_path} descends into non-group"
        assert segment in commands, f"Unknown CLI command path: {' '.join(command_path)}"
        command = commands[segment]
    assert command_path, "Demo command did not include a molecule-ranker command"
