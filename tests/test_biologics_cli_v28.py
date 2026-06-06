from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.biologics.dashboard import build_biologics_dashboard_pages
from molecule_ranker.cli import app

VALID_HEAVY_SEQUENCE = "ACDEFGHIKLMNPQRSTVWY" * 6


def test_biologics_cli_help_works() -> None:
    result = CliRunner().invoke(app, ["biologics", "--help"])

    assert result.exit_code == 0
    for command in [
        "retrieve",
        "validate-sequence",
        "number-sequence",
        "annotate-cdr",
        "assess-developability",
        "assess-novelty",
        "generate",
        "rank",
        "report",
        "validate-guardrails",
    ]:
        assert command in result.output


def test_biologics_validate_sequence_command() -> None:
    result = CliRunner().invoke(
        app,
        [
            "biologics",
            "validate-sequence",
            "--sequence",
            VALID_HEAVY_SEQUENCE,
            "--sequence-id",
            "seq-cli-heavy",
            "--chain-type",
            "heavy",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["sequence_id"] == "seq-cli-heavy"
    assert payload["valid"] is True
    assert payload["rejected"] is False
    assert payload["limitations"]


def test_biologics_report_command() -> None:
    result = CliRunner().invoke(
        app,
        [
            "biologics",
            "report",
            "--biologic-id",
            "bio-cli-report",
            "--name",
            "Source-backed antibody",
            "--target-symbol",
            "TNF",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    report_card = payload["report_card"]
    assert report_card["biologic_id"] == "bio-cli-report"
    assert report_card["name"] == "Source-backed antibody"
    assert report_card["target_symbols"] == ["TNF"]
    assert any(
        "Generated antibodies are computational hypotheses only" in limitation
        for limitation in report_card["limitations"]
    )


def test_biologics_dashboard_labels_generated_antibodies() -> None:
    labels = {page["label"] for page in build_biologics_dashboard_pages()}

    assert "Generated antibody hypotheses" in labels
