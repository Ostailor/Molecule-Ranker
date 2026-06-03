from __future__ import annotations

from molecule_ranker.codex_backbone.guardrails import output_guardrail_warnings


def test_synthesis_instruction_language_is_flagged_without_steps() -> None:
    warnings = output_guardrail_warnings(
        "This synthetic output asks for synthesis routes.",
        task_type="draft_dossier",
    )

    assert any("synthesis route" in warning for warning in warnings)
