from __future__ import annotations

from pathlib import Path

ADMIN_DOC_DIR = Path("docs/admin")
ADMIN_DOCS = [
    "users_and_roles.md",
    "organizations_and_teams.md",
    "project_permissions.md",
    "service_accounts.md",
    "audit_logs.md",
    "integration_credentials.md",
    "artifact_storage.md",
    "retention_and_delete.md",
    "security_checklist.md",
]
REQUIRED_TOPICS = [
    "RBAC matrix",
    "permission",
    "service token lifecycle",
    "secret-ref",
    "audit review",
    "project export",
    "delete",
    "incident response",
]
FORBIDDEN_TEXT = [
    "real credential",
    "sk-",
    "-----begin",
    "password123",
    "disable auth",
    "bypass rbac",
]


def test_admin_docs_exist() -> None:
    for name in ADMIN_DOCS:
        path = ADMIN_DOC_DIR / name
        assert path.exists(), f"missing admin doc: {name}"
        text = path.read_text()
        assert "molecule-ranker" in text
        assert "```bash" in text


def test_admin_docs_cover_required_topics_and_avoid_secret_examples() -> None:
    combined = "\n".join((ADMIN_DOC_DIR / name).read_text() for name in ADMIN_DOCS)
    lowered = combined.lower()
    for topic in REQUIRED_TOPICS:
        assert topic.lower() in lowered, f"missing topic: {topic}"
    for forbidden in FORBIDDEN_TEXT:
        assert forbidden in {"sk-"} or forbidden not in lowered
    assert "sk-" not in lowered
