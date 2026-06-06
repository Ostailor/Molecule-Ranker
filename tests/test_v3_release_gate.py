from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.autonomy_validation.boundary_tests import (
    run_autonomy_boundary_fixtures,
)
from molecule_ranker.cli import app
from molecule_ranker.v3.release_gate import (
    EXPENSIVE_V3_RELEASE_EVIDENCE_CHECKS,
    V3ReleaseGateConfig,
    run_v3_release_gate,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v3_release_gate_passes_synthetic_good_state(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)

    report = run_v3_release_gate(
        V3ReleaseGateConfig(
            root_dir=ROOT,
            output_dir=tmp_path / "gate-output",
            evidence_dir=evidence,
            run_expensive_checks=False,
        )
    )

    assert report["status"] == "pass"
    assert _check(report, "version_is_3_0_0")["status"] == "pass"
    assert _check(report, "v3_validation_passes")["status"] == "pass"
    assert _check(report, "required_demos_present")["status"] == "pass"
    assert (tmp_path / "gate-output" / "v3_release_gate.json").exists()
    assert (tmp_path / "gate-output" / "v3_release_gate.md").exists()


def test_v3_release_gate_fails_if_version_wrong(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from molecule_ranker.v3 import release_gate

    monkeypatch.setattr(release_gate, "__version__", "2.9.9")

    report = run_v3_release_gate(
        V3ReleaseGateConfig(
            root_dir=ROOT,
            output_dir=tmp_path / "gate-output",
            evidence_dir=_passing_evidence(tmp_path),
            run_expensive_checks=False,
        )
    )

    assert report["status"] == "fail"
    assert _check(report, "version_is_3_0_0")["status"] == "fail"


def test_v3_release_gate_fails_if_boundary_test_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from molecule_ranker.v3 import release_gate

    boundary_result = run_autonomy_boundary_fixtures()
    failed_test = boundary_result.boundary_tests[0].model_copy(
        update={"passed": False, "findings": ["synthetic boundary failure"]}
    )
    failed_result = boundary_result.model_copy(
        update={
            "boundary_tests": [failed_test, *boundary_result.boundary_tests[1:]],
            "unsafe_action_escape_rate": 1.0,
            "passed": False,
        }
    )
    monkeypatch.setattr(release_gate, "run_autonomy_boundary_fixtures", lambda: failed_result)

    report = run_v3_release_gate(
        V3ReleaseGateConfig(
            root_dir=ROOT,
            output_dir=tmp_path / "gate-output",
            evidence_dir=_passing_evidence(tmp_path),
            run_expensive_checks=False,
        )
    )

    assert report["status"] == "fail"
    assert _check(report, "autonomy_boundaries_pass")["status"] == "fail"


def test_v3_release_gate_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    evidence = _passing_evidence(tmp_path)
    output_dir = tmp_path / "cli-output"

    result = CliRunner().invoke(
        app,
        [
            "v3",
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
    assert (output_dir / "v3_release_gate.json").exists()
    assert (output_dir / "v3_release_gate.md").exists()


def _passing_evidence(tmp_path: Path) -> Path:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for check_id in EXPENSIVE_V3_RELEASE_EVIDENCE_CHECKS:
        (evidence / f"{check_id}.json").write_text(
            json.dumps({"status": "pass", "source": "synthetic_test"}, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return evidence


def _check(report: dict, check_id: str) -> dict:
    return next(check for check in report["checks"] if check["check_id"] == check_id)
