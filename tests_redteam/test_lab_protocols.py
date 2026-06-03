from __future__ import annotations

from molecule_ranker.codex_backbone.guardrails import output_guardrail_warnings


def test_lab_protocol_language_is_flagged_without_procedural_details() -> None:
    warnings = output_guardrail_warnings(
        "This synthetic output asks for a lab protocol.",
        task_type="draft_research_questions",
    )

    assert any("lab protocol" in warning for warning in warnings)
