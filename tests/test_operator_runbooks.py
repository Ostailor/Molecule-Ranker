from __future__ import annotations

from pathlib import Path

RUNBOOK_DIR = Path("docs/runbooks")
RUNBOOKS = [
    "deployment.md",
    "local_development.md",
    "production_config.md",
    "backup_restore.md",
    "worker_operations.md",
    "codex_worker.md",
    "integration_sync.md",
    "security_incidents.md",
    "data_retention.md",
    "troubleshooting.md",
    "release_process.md",
]
REQUIRED_SECTIONS = [
    "## Purpose",
    "## Prerequisites",
    "## Commands",
    "## Expected Output",
    "## Failure Modes",
    "## Rollback Steps",
    "## Safety/Security Notes",
]
FORBIDDEN_PHRASES = [
    "bypass security",
    "disable auth",
    "ignore rbac",
    "real secret",
    "patient treatment",
    "dosage",
    "synthesis instructions",
    "lab protocol",
]


def test_operator_runbooks_exist_with_required_sections() -> None:
    for name in RUNBOOKS:
        path = RUNBOOK_DIR / name
        assert path.exists(), f"missing runbook: {name}"
        text = path.read_text()
        for section in REQUIRED_SECTIONS:
            assert section in text, f"{name} missing {section}"
        assert "molecule-ranker" in text
        assert "```bash" in text


def test_operator_runbooks_do_not_include_unsafe_guidance_or_secrets() -> None:
    combined = "\n".join((RUNBOOK_DIR / name).read_text() for name in RUNBOOKS).lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in combined
    assert "sk-" not in combined
    assert "-----begin" not in combined
