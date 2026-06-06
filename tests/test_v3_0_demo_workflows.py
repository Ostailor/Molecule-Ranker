from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "examples" / "v3_0"
DEMO_NAMES = [
    "mocked_full_discovery_loop",
    "dry_run_full_discovery_loop",
    "read_only_live_small_molecule_loop",
    "mocked_biologics_loop",
    "campaign_copilot_event_loop",
    "integration_dry_run_loop",
    "result_certification_demo",
    "boundary_test_demo",
]


def test_v3_0_demo_workflows_are_complete_and_safe() -> None:
    assert DEMO_ROOT.is_dir()
    combined_text_parts: list[str] = []
    for demo_name in DEMO_NAMES:
        demo_dir = DEMO_ROOT / demo_name
        assert demo_dir.is_dir(), demo_name
        for relative in ["README.md", "run.sh", "expected_output_manifest.json"]:
            path = demo_dir / relative
            assert path.exists(), f"{demo_name}/{relative}"
            combined_text_parts.append(path.read_text(encoding="utf-8"))

        manifest = json.loads((demo_dir / "expected_output_manifest.json").read_text())
        assert manifest["demo_id"] == demo_name
        assert manifest["synthetic_or_mocked"] is True
        assert manifest["no_clinical_claims"] is True
        assert manifest["no_protocols_synthesis_or_dosing"] is True
        assert manifest["what_output_means"]
        assert manifest["what_output_does_not_prove"]

    combined_text = "\n".join(combined_text_parts)
    assert "synthetic" in combined_text.lower()
    assert "mocked" in combined_text.lower()
    assert not re.search(r"\bPMID\s*:?\s*\d+", combined_text, flags=re.IGNORECASE)
    assert not re.search(r"\b10\.\d{4,9}/\S+", combined_text)
    for forbidden in [
        "validated binder",
        "real assay result",
        "synthesis route",
        "dosing regimen",
        "lab protocol steps",
        "clinically validated",
    ]:
        assert forbidden not in combined_text.lower()


def test_v3_0_demo_scripts_are_shell_syntax_valid() -> None:
    for script in sorted(DEMO_ROOT.glob("*/run.sh")):
        subprocess.run(["bash", "-n", str(script)], check=True, cwd=ROOT)


def test_v3_0_mocked_demo_runs_in_validation_mode(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MOLECULE_RANKER_CMD": "uv run molecule-ranker",
            "OUTPUT_DIR": str(tmp_path),
            "VALIDATION_MODE": "1",
        }
    )

    subprocess.run(
        ["bash", str(DEMO_ROOT / "mocked_full_discovery_loop" / "run.sh")],
        check=True,
        cwd=ROOT,
        env=env,
        text=True,
    )

    assert (tmp_path / "discover_result.json").exists()
    assert (tmp_path / "v3_result_bundle.json").exists()
    assert (tmp_path / "v3_human_governance_matrix.json").exists()
    result = json.loads((tmp_path / "discover_result.json").read_text())
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert result["status"] == "succeeded"
    assert result["mode"] == "mocked"
    assert result["external_writes_performed"] == 0
    assert trace["safe_defaults"]["external_writes_enabled"] is False
