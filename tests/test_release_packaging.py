from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker import __version__
from molecule_ranker.cli import app
from molecule_ranker.contracts import (
    API_CONTRACT_VERSION as API_CONTRACT_REGISTRY_VERSION,
)
from molecule_ranker.contracts import (
    ARTIFACT_CONTRACT_VERSION as ARTIFACT_CONTRACT_REGISTRY_VERSION,
)
from molecule_ranker.release.checks import run_release_checks
from molecule_ranker.release.manifest import build_release_manifest
from molecule_ranker.release.notes import render_release_notes

ROOT = Path(__file__).resolve().parents[1]


def test_release_manifest_contains_v1_packaging_fields() -> None:
    manifest = build_release_manifest(ROOT)

    assert manifest["version"] == __version__ == "1.2.0"
    assert manifest["git_commit"]
    assert manifest["build_timestamp"].endswith("Z")
    assert manifest["artifact_contract_version"] == ARTIFACT_CONTRACT_REGISTRY_VERSION
    assert manifest["api_contract_version"] == API_CONTRACT_REGISTRY_VERSION
    assert len(manifest["dependency_lock_hash"]) == 64
    assert manifest["test_summary"]["status"] in {"not_recorded", "pass"}
    assert manifest["validation_summary"]["status"] in {"not_recorded", "pass", "fail"}
    assert manifest["known_limitations"]


def test_release_check_verifies_packaging_without_live_services() -> None:
    report = run_release_checks(ROOT, run_commands=False)
    failures = [check for check in report["checks"] if check["status"] == "fail"]

    assert report["status"] == "pass"
    assert failures == []
    assert {check["check_id"] for check in report["checks"]} >= {
        "version",
        "artifact_contracts",
        "api_contracts",
        "security_audit",
        "guardrail_audit",
        "docs",
        "runbooks",
        "docker_build_available",
        "no_plaintext_secrets",
    }


def test_release_notes_render_v1_stabilization_scope() -> None:
    notes = render_release_notes(build_release_manifest(ROOT))

    assert "# molecule-ranker 1.2.0 Release Notes" in notes
    assert "validated internal research platform MVP" in notes
    assert "research use only" in notes
    assert "no medical advice" in notes
    assert "generated molecules require validation" in notes


def test_release_packaging_cli_writes_manifest_and_notes(tmp_path: Path) -> None:
    runner = CliRunner()
    manifest_path = tmp_path / "release_manifest.json"
    notes_path = tmp_path / "RELEASE_NOTES.md"

    manifest_result = runner.invoke(
        app,
        ["release", "manifest", "--root", str(ROOT), "--output", str(manifest_path)],
    )
    notes_result = runner.invoke(
        app,
        ["release", "notes", "--root", str(ROOT), "--output", str(notes_path)],
    )

    assert manifest_result.exit_code == 0, manifest_result.output
    assert notes_result.exit_code == 0, notes_result.output
    assert json.loads(manifest_path.read_text())["version"] == "1.2.0"
    assert "molecule-ranker 1.2.0 Release Notes" in notes_path.read_text()


def test_release_check_cli_reports_machine_readable_results() -> None:
    result = CliRunner().invoke(app, ["release", "check", "--root", str(ROOT), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "pass"
    assert payload["version"] == "1.2.0"
