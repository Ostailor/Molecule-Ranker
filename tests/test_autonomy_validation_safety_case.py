from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.autonomy_validation.safety_case import (
    SAFETY_CASE_JSON,
    SAFETY_CASE_MARKDOWN,
    build_v3_safety_case_report,
    render_v3_safety_case_markdown,
    write_v3_safety_case_report,
)

NOW = datetime(2026, 6, 6, tzinfo=UTC)


def test_v3_safety_case_claims_have_supporting_artifact_refs() -> None:
    report = build_v3_safety_case_report(now=lambda: NOW)

    assert len(report.claims) == 10
    for claim in report.claims:
        assert claim["supporting_validation_artifacts"]
        assert claim["boundary_tests"]
        assert claim["residual_risks"]
        assert claim["limitations"]
        assert claim["supported"] is True


def test_v3_safety_case_states_platform_limitations() -> None:
    report = build_v3_safety_case_report(now=lambda: NOW)
    markdown = render_v3_safety_case_markdown(report)

    assert "not a regulatory safety case" in markdown
    assert "not clinical validation" in markdown
    assert "autonomy/platform safety evidence" in markdown
    assert "No claim establishes binding" in markdown


def test_write_v3_safety_case_outputs_json_and_markdown(tmp_path: Path) -> None:
    report = write_v3_safety_case_report(tmp_path, now=lambda: NOW)

    json_path = tmp_path / SAFETY_CASE_JSON
    markdown_path = tmp_path / SAFETY_CASE_MARKDOWN
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["safety_case_id"] == report.safety_case_id
    assert payload["metadata"]["output_files"] == [
        SAFETY_CASE_JSON,
        SAFETY_CASE_MARKDOWN,
    ]
    assert markdown_path.read_text(encoding="utf-8").startswith("# V3 Safety Case")
