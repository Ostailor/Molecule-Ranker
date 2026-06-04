from __future__ import annotations

import pytest

from molecule_ranker.agent_repair.diagnosis import (
    FailureDiagnosisAgent as CoreFailureDiagnosisAgent,
)
from molecule_ranker.agent_repair.diagnosis import (
    diagnose_failure,
)
from molecule_ranker.agents.failure_diagnosis import FailureDiagnosisAgent
from molecule_ranker.runtime_agents.schemas import RuntimeToolResult


@pytest.mark.parametrize(
    ("kwargs", "category"),
    [
        (
            {
                "failed_validation_report": {
                    "validation_id": "validation-1",
                    "status": "failed",
                    "errors": ["invalid schema: missing required field unit"],
                }
            },
            "invalid_schema",
        ),
        ({"missing_artifact": {"artifact_id": "artifact-missing"}}, "missing_artifact"),
        (
            {
                "failed_tool_result": {
                    "result_id": "result-1",
                    "status": "failed",
                    "error_summary": "External provider unavailable: 503.",
                }
            },
            "external_unavailable",
        ),
        (
            {
                "failed_tool_result": {
                    "result_id": "result-1",
                    "status": "failed",
                    "error_summary": "403 permission denied.",
                }
            },
            "permission_denied",
        ),
        (
            {
                "failed_tool_result": {
                    "result_id": "result-1",
                    "status": "policy_blocked",
                    "error_summary": "Tool policy blocked this action.",
                }
            },
            "policy_blocked",
        ),
        (
            {
                "failed_guardrail_report": {
                    "guardrail_id": "guardrail-1",
                    "allowed": False,
                    "violations": [{"code": "unsafe_output"}],
                }
            },
            "guardrail_failed",
        ),
        (
            {"failed_job": {"job_id": "job-1", "status": "failed", "error": "job timed out"}},
            "timeout",
        ),
        (
            {
                "failed_job": {
                    "job_id": "job-2",
                    "status": "failed",
                    "error": "resource exhausted: quota exceeded",
                }
            },
            "resource_exhausted",
        ),
        (
            {
                "failed_tool_result": {
                    "result_id": "result-1",
                    "status": "failed",
                    "error_summary": "Tool crashed.",
                }
            },
            "tool_error",
        ),
        (
            {
                "failed_validation_report": {
                    "validation_id": "validation-2",
                    "status": "failed",
                    "errors": ["validation failed: value out of range"],
                }
            },
            "validation_failed",
        ),
        (
            {
                "failed_codex_output": {
                    "output_id": "codex-1",
                    "error_summary": "JSON parse failed for Codex output.",
                }
            },
            "parse_error",
        ),
        (
            {
                "failed_codex_output": {
                    "output_id": "codex-2",
                    "error_summary": "Unsafe output contained synthesis instructions.",
                }
            },
            "unsafe_output",
        ),
        (
            {
                "related_artifacts": [
                    {"artifact_id": "a1", "summary": "inconsistent artifacts conflict"},
                ]
            },
            "inconsistent_artifacts",
        ),
        (
            {
                "failed_validation_report": {
                    "validation_id": "validation-3",
                    "errors": ["reproducibility check failed"],
                }
            },
            "reproducibility_failure",
        ),
    ],
)
def test_failure_diagnosis_classifies_major_categories(kwargs, category) -> None:  # type: ignore[no-untyped-def]
    diagnosis = CoreFailureDiagnosisAgent().diagnose(**kwargs)

    assert diagnosis.failure_category == category
    assert diagnosis.evidence
    assert diagnosis.root_cause_summary
    assert "Deterministic evidence decides" in diagnosis.warnings[0]


def test_guardrail_failure_is_not_hidden_by_policy_or_tool_context() -> None:
    diagnosis = diagnose_failure(
        failed_tool_result=_tool_result(
            status="policy_blocked",
            error_summary="Policy blocked after guardrail failed.",
        ),
        failed_guardrail_report={
            "guardrail_id": "guardrail-1",
            "allowed": False,
            "violations": [{"code": "generated_overclaim"}],
        },
    )

    assert diagnosis.failure_category == "guardrail_failed"
    assert diagnosis.repairability == "approval_required"
    assert any("must not be hidden" in warning for warning in diagnosis.warnings)


def test_unknown_failure_requires_human_input_when_uncertain() -> None:
    diagnosis = diagnose_failure(exception_trace="unexpected blank failure")

    assert diagnosis.failure_category == "unknown"
    assert diagnosis.repairability == "human_input_required"
    assert diagnosis.confidence < 0.5


def test_pipeline_agent_wrapper_records_diagnosis_in_context() -> None:
    from molecule_ranker.agents.base import PipelineContext

    context = PipelineContext(
        disease_input="test",
        config={
            "failure_diagnosis": {
                "failed_tool_result": {
                    "result_id": "result-1",
                    "status": "failed",
                    "error_summary": "Tool crashed.",
                }
            }
        },
    )

    updated = FailureDiagnosisAgent().run(context)

    assert updated.config["failure_diagnosis_result"]["failure_category"] == "tool_error"


def _tool_result(*, status: str, error_summary: str) -> RuntimeToolResult:
    from datetime import UTC, datetime

    return RuntimeToolResult(
        result_id="result-1",
        step_id="step-1",
        tool_name="run_ranking",
        status=status,  # type: ignore[arg-type]
        output={},
        artifact_ids=[],
        job_ids=[],
        error_summary=error_summary,
        warnings=[],
        started_at=datetime(2026, 6, 4, 12, tzinfo=UTC),
        completed_at=datetime(2026, 6, 4, 12, tzinfo=UTC),
        metadata={},
    )
