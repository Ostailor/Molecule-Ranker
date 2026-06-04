from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.subagents.critique import CritiqueAndReviseWorkflow, review_result
from molecule_ranker.subagents.schemas import SubagentResult


def test_critique_catches_missing_citation() -> None:
    result = _result(
        output_json={
            "summary": "Candidate activity claim.",
            "claims": [{"text": "Candidate A is active."}],
        },
        output_text="Candidate A is active based on reviewed evidence.",
        artifact_ids=["evidence-artifact-1"],
        metadata={"artifact_provenance": {"evidence-artifact-1": "review workspace"}},
    )

    critiques = review_result(result)

    evidence_critiques = [
        critique for critique in critiques if critique.critique_type == "evidence_grounding"
    ]
    assert evidence_critiques
    assert evidence_critiques[0].passed is False
    assert "missing a citation" in evidence_critiques[0].findings[0]


def test_revision_fixes_schema() -> None:
    result = _result(output_json={"summary": "Initial output."})
    schema = {
        "type": "object",
        "required": ["summary", "findings", "recommended_next_actions"],
        "properties": {
            "summary": {"type": "string"},
            "findings": {"type": "array"},
            "recommended_next_actions": {"type": "array"},
        },
    }

    record = CritiqueAndReviseWorkflow().run(
        parent_session_id="session-1",
        result=result,
        expected_output_schema=schema,
    )

    assert len(record.result_versions) == 2
    revised = record.result_versions[-1]
    assert revised.output_json == {
        "summary": "Initial output.",
        "findings": [],
        "recommended_next_actions": [],
    }
    assert any(
        critique.critique_type == "output_schema_validity" and not critique.passed
        for critique in record.critiques
    )
    assert record.consensus.consensus_status == "agreed"
    assert record.consensus.human_review_required is False
    assert record.consensus.metadata["result_versions"] == [
        record.result_versions[0].result_id,
        revised.result_id,
    ]


def test_unresolved_disagreement_escalates() -> None:
    result = _result(
        status="guardrail_failed",
        output_json={"summary": "Unsafe output."},
        guardrail_findings=[{"code": "generated_overclaim"}],
    )

    record = CritiqueAndReviseWorkflow().run(
        parent_session_id="session-1",
        result=result,
        risk_level="high",
    )

    assert len(record.result_versions) == 1
    assert record.consensus.consensus_status == "requires_human_review"
    assert record.consensus.human_review_required is True
    assert record.consensus.metadata["guardrail_failures_non_overridable"] is True
    assert "Guardrail failure cannot be overridden" in record.consensus.disagreements[0]


def _result(**overrides) -> SubagentResult:  # type: ignore[no-untyped-def]
    payload = {
        "result_id": "result-1",
        "task_id": "task-1",
        "subagent_id": "evidence-reviewer",
        "status": "succeeded",
        "output_json": {"summary": "Output."},
        "output_text": "Output.",
        "artifact_ids": [],
        "tool_usage_ids": ["tool-usage-1"],
        "confidence": 0.8,
        "warnings": [],
        "guardrail_findings": [],
        "created_at": datetime.now(UTC),
        "metadata": {},
    }
    return SubagentResult(**{**payload, **overrides})
