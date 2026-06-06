from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "examples" / "v3_demo"


def test_v3_demo_assets_are_synthetic_and_complete() -> None:
    required = {
        "README.md",
        "run_mocked_demo.sh",
        "run_readonly_live_demo.sh",
        "run_validation.sh",
        "synthetic_inputs/demo_user_goals.json",
        "synthetic_inputs/synthetic_project_context.json",
        "synthetic_inputs/copilot_event.json",
        "expected_outputs/expected_artifacts_manifest.json",
        "expected_outputs/guardrail_expectations.json",
    }

    assert DEMO_DIR.is_dir()
    for relative in required:
        assert (DEMO_DIR / relative).exists(), relative

    combined_text = "\n".join(
        (DEMO_DIR / relative).read_text(encoding="utf-8") for relative in sorted(required)
    )
    assert "synthetic" in combined_text.lower()
    assert not re.search(r"\bPMID\s*:?\s*\d+", combined_text, flags=re.IGNORECASE)
    assert not re.search(r"\b10\.\d{4,9}/\S+", combined_text)
    assert "validated binder" not in combined_text.lower()
    assert "validated active" not in combined_text.lower()
    assert "real assay result" not in combined_text.lower()

    goals = json.loads(
        (DEMO_DIR / "synthetic_inputs/demo_user_goals.json").read_text(encoding="utf-8")
    )
    assert goals["synthetic"] is True
    assert {goal["scenario_id"] for goal in goals["goals"]} >= {
        "v3_full_demo_mocked",
        "small_molecule_generation_mocked_e2e",
        "biologics_mocked_e2e",
        "integration_dry_run_e2e",
        "campaign_copilot_monitoring",
    }


def test_v3_demo_scripts_are_shell_syntax_valid() -> None:
    for script in [
        "run_mocked_demo.sh",
        "run_readonly_live_demo.sh",
        "run_validation.sh",
    ]:
        subprocess.run(["bash", "-n", str(DEMO_DIR / script)], check=True, cwd=ROOT)


def test_v3_mocked_demo_completes_in_validation_mode(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MOLECULE_RANKER_CMD": "uv run molecule-ranker",
            "OUTPUT_DIR": str(tmp_path),
            "VALIDATION_MODE": "1",
        }
    )

    subprocess.run(
        ["bash", str(DEMO_DIR / "run_mocked_demo.sh")],
        check=True,
        cwd=ROOT,
        env=env,
        text=True,
    )

    expected_outputs = [
        "small_molecule_disease_to_result_bundle.json",
        "generated_small_molecule_hypothesis.json",
        "biologics_mocked_workflow.json",
        "integration_dry_run_workflow.json",
        "campaign_copilot_event_workflow.json",
        "guardrail_validation.json",
        "v3_readiness_and_rc.json",
        "demo_run_manifest.json",
        "v3_rc/v3_readiness_report.md",
        "v3_rc/v3_rc_result_bundle.zip",
    ]
    for relative in expected_outputs:
        assert (tmp_path / relative).exists(), relative

    rc_payload = json.loads((tmp_path / "v3_readiness_and_rc.json").read_text())
    assert rc_payload["status"] == "passed"
    assert rc_payload["readiness_status"] == "ready"
