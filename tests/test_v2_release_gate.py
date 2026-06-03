from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.v2.release_gate import (
    EXPENSIVE_EVIDENCE_CHECKS,
    V2_RELEASE_GATE_JSON,
    V2_RELEASE_GATE_MARKDOWN,
    V2ReleaseGateConfig,
    _slo_check,
    run_v2_release_gate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v2_release_gate_passes_synthetic_setup(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    output_dir = tmp_path / "gate-output"

    report = run_v2_release_gate(
        V2ReleaseGateConfig(
            root_dir=ROOT,
            output_dir=output_dir,
            evidence_dir=evidence,
            run_expensive_checks=False,
        )
    )

    assert report["status"] == "pass"
    assert (output_dir / V2_RELEASE_GATE_JSON).exists()
    assert (output_dir / V2_RELEASE_GATE_MARKDOWN).exists()
    assert _check(report, "red_team_suite_passes")["status"] == "pass"
    assert _check(report, "docs_exist")["status"] == "pass"
    assert _check(report, "training_materials_exist")["status"] == "pass"


def test_v2_release_gate_missing_required_check_fails(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    root = tmp_path / "empty-root"
    root.mkdir()

    report = run_v2_release_gate(
        V2ReleaseGateConfig(
            root_dir=root,
            output_dir=tmp_path / "gate-output",
            evidence_dir=evidence,
            run_expensive_checks=False,
        )
    )

    assert report["status"] == "fail"
    assert _check(report, "docs_exist")["status"] == "fail"
    assert _check(report, "training_materials_exist")["status"] == "fail"


def test_v2_release_gate_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    output_dir = tmp_path / "cli-output"

    result = CliRunner().invoke(
        app,
        [
            "v2",
            "release-gate",
            "--root",
            str(ROOT),
            "--output-dir",
            str(output_dir),
            "--evidence-dir",
            str(evidence),
            "--no-run-expensive-checks",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert (output_dir / V2_RELEASE_GATE_JSON).exists()
    assert (output_dir / V2_RELEASE_GATE_MARKDOWN).exists()


def test_v2_release_gate_slo_check_requires_healthy_report(tmp_path: Path) -> None:
    check = _slo_check(tmp_path)

    assert check["status"] == "pass"
    assert check["details"]["status"] == "pass"


def _passing_evidence(tmp_path: Path) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for check_id in EXPENSIVE_EVIDENCE_CHECKS:
        (evidence / f"{check_id}.json").write_text(
            json.dumps({"status": "pass", "source": "synthetic_test"}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return evidence


def _check(report: dict, check_id: str) -> dict:
    return next(check for check in report["checks"] if check["check_id"] == check_id)
