from __future__ import annotations

from pathlib import Path

V3_DOCS = (
    "index.md",
    "quickstart.md",
    "run_discovery_workflow.md",
    "interpret_result_bundle.md",
    "human_governance.md",
    "agent_runtime.md",
    "generated_hypotheses.md",
    "biologics_track.md",
    "integrations.md",
    "campaign_copilot.md",
    "validation_and_certification.md",
    "safety_boundaries.md",
    "admin_operations.md",
    "troubleshooting.md",
    "faq.md",
)
REQUIRED_BOUNDARY_PHRASES = (
    "No medical advice.",
    "No clinical validation.",
    "No lab protocols.",
    "No synthesis instructions.",
    "No dosing.",
    "Generated hypotheses require independent validation and human review.",
    "Codex output is not scientific truth.",
)


def test_v3_docs_exist() -> None:
    docs_dir = Path("docs/v3")

    missing = [filename for filename in V3_DOCS if not (docs_dir / filename).exists()]

    assert missing == []


def test_v3_docs_repeat_required_boundaries() -> None:
    docs_dir = Path("docs/v3")

    missing: dict[str, list[str]] = {}
    for filename in V3_DOCS:
        text = (docs_dir / filename).read_text(encoding="utf-8")
        missing_phrases = [phrase for phrase in REQUIRED_BOUNDARY_PHRASES if phrase not in text]
        if missing_phrases:
            missing[filename] = missing_phrases

    assert missing == {}

