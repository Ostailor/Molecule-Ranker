from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.runtime_agents.recovery import (
    COMMON_RUNTIME_FAILURE_TYPES,
    RuntimeFailureRecovery,
    diagnose_failure,
    recover_failure,
)
from molecule_ranker.runtime_agents.schemas import RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


@pytest.mark.parametrize("failure_type", COMMON_RUNTIME_FAILURE_TYPES)
def test_each_common_failure_produces_safe_recovery_suggestion(failure_type: str) -> None:
    suggestion = diagnose_failure(
        {
            "failure_type": failure_type,
            "error_summary": f"Runtime failed with {failure_type}",
            "metadata": {"artifact_id": "artifact-1"},
        }
    )

    assert suggestion.failure_type == failure_type
    assert suggestion.diagnosis
    assert suggestion.safe_next_actions
    assert all(action.strip() for action in suggestion.safe_next_actions)
    assert "Never fabricate missing data." in suggestion.guardrails


def test_no_candidates_failure_suggests_broader_target_or_identifier() -> None:
    suggestion = diagnose_failure("No candidates found for the disease identifier.")

    assert suggestion.failure_type == "no_candidates_found"
    actions = " ".join(suggestion.safe_next_actions).lower()
    assert "broader target limit" in actions
    assert "different disease identifier" in actions


def test_auto_recovery_executes_only_safe_registered_tool() -> None:
    calls: list[str] = []

    def handler(step: RuntimeActionStep, _spec: Any) -> dict[str, Any]:
        calls.append(step.tool_name)
        return {"output": {"validation_error_report": "report-artifact-1"}}

    result = recover_failure(
        {
            "failure_type": "assay_import_validation_failed",
            "error_summary": "Assay import validation failed.",
            "metadata": {"assay_artifact_id": "assay-upload-1"},
        },
        autonomy_level="execute_safe_tools",
        tool_handlers={"summarize_assay_results": handler},
    )

    assert result.auto_recovery_allowed is True
    assert result.auto_recovery_attempted is True
    assert calls == ["summarize_assay_results"]
    assert result.tool_result is not None
    assert result.tool_result.status == "succeeded"
    assert result.tool_result.tool_name == "summarize_assay_results"


def test_auto_recovery_does_not_execute_unsafe_or_approval_required_tool() -> None:
    calls: list[str] = []

    result = RuntimeFailureRecovery().recover(
        {
            "failure_type": "literature_unavailable",
            "error_summary": "Literature provider unavailable.",
            "metadata": {"strict": True},
        },
        autonomy_level="execute_safe_tools",
        tool_handlers={
            "run_literature_update": lambda step, spec: calls.append(step.tool_name) or {}
        },
    )

    assert result.suggestion.recovery_tool_name == "run_literature_update"
    assert result.auto_recovery_allowed is False
    assert result.auto_recovery_attempted is False
    assert result.tool_result is None
    assert calls == []
    assert any("approval" in warning.lower() for warning in result.warnings)


def test_permission_denied_requests_admin_without_bypass() -> None:
    suggestion = diagnose_failure(
        {"failure_type": "permission_denied", "error_summary": "403 permission denied."}
    )

    assert suggestion.approval_required is True
    text = " ".join([suggestion.diagnosis, *suggestion.safe_next_actions]).lower()
    assert "admin" in text or "authorized user" in text
    assert "bypass" in text
    assert "do not bypass" in text


def test_recovery_suggestions_do_not_fabricate_scientific_data() -> None:
    recovery = RuntimeFailureRecovery()
    forbidden_fragments = [
        "PMID:",
        "DOI ",
        "IC50",
        "EC50",
        "SMILES",
        "InChI",
        "assay result created",
        "molecule created",
    ]

    for failure_type in COMMON_RUNTIME_FAILURE_TYPES:
        suggestion = recovery.diagnose(
            {"failure_type": failure_type, "error_summary": "Runtime failure."}
        )
        text = suggestion.model_dump_json()
        assert not any(fragment in text for fragment in forbidden_fragments)


def test_recovery_tool_must_be_registered_and_permissioned() -> None:
    registry = RuntimeToolRegistry.default()

    for failure_type in COMMON_RUNTIME_FAILURE_TYPES:
        suggestion = diagnose_failure({"failure_type": failure_type})
        if suggestion.recovery_tool_name is None:
            continue
        spec = registry.require(suggestion.recovery_tool_name)
        assert spec.required_permissions
        assert spec.side_effect_level in {
            "none",
            "artifact_write",
            "external_read",
            "external_write",
            "db_write",
            "codex_subprocess",
        }
