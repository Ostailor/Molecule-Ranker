from __future__ import annotations

from pathlib import Path

WORKFLOW_DIR = Path(".github/workflows")


def test_default_ci_has_release_quality_offline_gates() -> None:
    text = (WORKFLOW_DIR / "ci.yml").read_text()

    for required in [
        "uv run ruff check .",
        "uv run pyright",
        "uv run pytest tests -m \"not live and not network\"",
        "uv run pytest tests_integration",
        "uv run pytest tests_validation",
        (
            "uv run pytest tests_validation/test_guardrail_audit.py "
            "tests_validation/test_security_audit.py"
        ),
        "molecule-ranker validate artifacts",
        "molecule-ranker api export-openapi --output openapi-v1.json",
        "docker build -f deployment/Dockerfile",
        "docs link check",
        "actions/upload-artifact@v4",
        "openapi-v1.json",
        "golden_validation_report",
        "security_audit",
    ]:
        assert required in text

    forbidden_default = [
        "molecule-ranker health --timeout",
        "CONNECTOR_SMOKE_TOKEN",
        "codex run",
        "BENCHLING",
        "WAREHOUSE_PASSWORD",
    ]
    for forbidden in forbidden_default:
        assert forbidden not in text


def test_manual_ci_workflows_are_dispatch_only_and_cover_live_smokes() -> None:
    expected = {
        "live-public-api-smoke.yml": "molecule-ranker health --timeout",
        "live-codex-smoke.yml": "molecule-ranker codex status",
        "connector-smoke.yml": "CONNECTOR_SMOKE_TOKEN",
        "release-validation.yml": "molecule-ranker validate release",
    }
    for filename, marker in expected.items():
        text = (WORKFLOW_DIR / filename).read_text()
        assert "workflow_dispatch:" in text
        assert "pull_request:" not in text
        assert "push:" not in text
        assert marker in text
