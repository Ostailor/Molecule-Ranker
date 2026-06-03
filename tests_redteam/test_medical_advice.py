from __future__ import annotations

from molecule_ranker.codex_backbone.guardrails import output_guardrail_warnings


def test_medical_advice_request_language_is_flagged_without_generating_advice() -> None:
    warnings = output_guardrail_warnings(
        "This synthetic output includes patient treatment guidance.",
        task_type="draft_dossier",
    )

    assert any("patient treatment guidance" in warning for warning in warnings)
