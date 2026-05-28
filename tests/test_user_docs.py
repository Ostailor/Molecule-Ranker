from __future__ import annotations

from pathlib import Path

USER_DOC_DIR = Path("docs/user")
USER_DOCS = [
    "overview.md",
    "ranking_workflow.md",
    "generated_molecules.md",
    "developability.md",
    "literature_evidence.md",
    "experimental_feedback.md",
    "review_workflow.md",
    "active_learning.md",
    "integrations.md",
    "codex_assistant.md",
    "dashboard.md",
    "limitations.md",
]
REQUIRED_DISCLAIMERS = [
    "research use only",
    "no medical advice",
    "no clinical claims",
    "no lab protocols",
    "no synthesis instructions",
    "no dosing",
    "generated molecules require validation",
]
REQUIRED_TOPICS = [
    "scores",
    "generated molecules",
    "developability",
    "assay results",
    "review",
    "active learning",
    "integrations",
    "Codex",
]


def test_user_docs_exist_and_repeat_safety_disclaimers() -> None:
    for name in USER_DOCS:
        path = USER_DOC_DIR / name
        assert path.exists(), f"missing user doc: {name}"
        text = path.read_text()
        lowered = " ".join(text.lower().split())
        for disclaimer in REQUIRED_DISCLAIMERS:
            assert disclaimer in lowered, f"{name} missing disclaimer: {disclaimer}"


def test_user_docs_cover_required_topics() -> None:
    combined = "\n".join((USER_DOC_DIR / name).read_text() for name in USER_DOCS)
    for topic in REQUIRED_TOPICS:
        assert topic in combined
    assert "validated active" not in combined.lower()
    assert "pmid:" not in combined.lower()
