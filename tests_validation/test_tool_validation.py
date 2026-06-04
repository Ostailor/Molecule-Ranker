from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.tools import run_tool_ecosystem_validation


def test_tool_validation_safe_package_works(tmp_path: Path) -> None:
    report = run_tool_ecosystem_validation(tmp_path)

    checks = {check.check_id: check for check in report.checks}
    assert report.status == "pass"
    assert checks["install_safe_local_tool_package"].status == "pass"
    assert checks["scan_safe_package"].details["scan_status"] == "passed"
    assert checks["approve_safe_package"].status == "pass"
    assert checks["enable_safe_package_for_project"].status == "pass"
    assert checks["runtime_agent_uses_approved_tool"].details["execution_status"] == "succeeded"


def test_tool_validation_unsafe_cases_blocked(tmp_path: Path) -> None:
    report = run_tool_ecosystem_validation(tmp_path)
    checks = {check.check_id: check for check in report.checks}

    expected_blocked = {
        "unsafe_package_quarantined_rejected": "env_file_access",
        "red_team_env_access_blocked": "env_file_access",
        "red_team_wildcard_network_blocked": "broad_network_wildcard",
        "red_team_fake_evidence_creator_blocked": "evidence_creation_without_validator",
        "red_team_mcp_protocol_prompt_blocked": "forbidden_biomedical_prompt_template",
        "red_team_external_write_without_approval_blocked": "external_write_without_approval",
        "red_team_malicious_manifest_blocked": "manifest_schema_invalid",
    }
    for check_id, finding_id in expected_blocked.items():
        check = checks[check_id]
        assert check.status == "pass", check.as_dict()
        assert finding_id in check.details["finding_ids"]

    assert checks["red_team_fake_citation_output_blocked"].status == "pass"
    assert "fake_citation" in checks["red_team_fake_citation_output_blocked"].details[
        "finding_codes"
    ]
    assert checks["red_team_tool_name_collision_blocked"].status == "pass"
    assert checks["red_team_tool_schema_mismatch_blocked"].status == "pass"


def test_tool_validation_guardrail_report_generated(tmp_path: Path) -> None:
    report = run_tool_ecosystem_validation(tmp_path)

    json_report = tmp_path / "tool_security_report.json"
    markdown_report = tmp_path / "tool_security_report.md"
    payload = json.loads(json_report.read_text(encoding="utf-8"))

    assert report.status == "pass"
    assert json_report.exists()
    assert markdown_report.exists()
    assert payload["status"] == "pass"
    assert payload["failed_count"] == 0
    assert "red_team_fake_citation_output_blocked" in markdown_report.read_text(
        encoding="utf-8"
    )


def test_tool_validation_is_repeatable(tmp_path: Path) -> None:
    output_dir = tmp_path / "tools"

    first = run_tool_ecosystem_validation(output_dir)
    second = run_tool_ecosystem_validation(output_dir)

    assert first.status == "pass"
    assert second.status == "pass"
    assert (output_dir / "tool_security_report.json").exists()
    assert (output_dir / "tool_security_report.md").exists()


def test_validate_tools_cli_runs(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["validate", "tools", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "tools"
    assert payload["status"] == "pass"
    assert (output_dir / "tool_security_report.json").exists()
    assert (output_dir / "tool_security_report.md").exists()
