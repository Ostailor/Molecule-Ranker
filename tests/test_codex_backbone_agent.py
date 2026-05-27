from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from molecule_ranker.agents.base import AgentExecutionError, PipelineContext
from molecule_ranker.agents.codex_backbone import CodexBackboneAgent
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.schemas import Disease, EvidenceItem, MoleculeCandidate, ScoreBreakdown, Target


class FakeCodexProvider:
    def __init__(self, statuses: list[str] | None = None) -> None:
        self.statuses = statuses or []
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        status = (
            self.statuses[len(self.tasks) - 1]
            if len(self.tasks) <= len(self.statuses)
            else "succeeded"
        )
        warnings = ["blocked unsupported claim"] if status == "guardrail_failed" else []
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status=status,  # type: ignore[arg-type]
            output_text=json.dumps({"summary": f"{task.task_id} output"}),
            output_json={"summary": f"{task.task_id} output"},
            stdout=json.dumps({"summary": f"{task.task_id} output"}),
            stderr="",
            return_code=0 if status == "succeeded" else 2,
            artifacts_read=[],
            artifacts_written=[],
            commands_observed=[],
            guardrail_warnings=warnings,
            usage_summary={},
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            metadata={"requested_task": task.metadata.get("requested_task")},
        )


def test_codex_backbone_agent_disabled_noop(tmp_path: Path) -> None:
    context = _context(tmp_path, enable=False)

    result = CodexBackboneAgent(provider=FakeCodexProvider()).run(context)

    assert result.config["codex_backbone_enabled"] is False
    assert result.config["codex_backbone_results"] == []
    assert result.traces[-1].agent_name == "CodexBackboneAgent"
    assert result.traces[-1].output_summary == "Codex backbone disabled."
    assert not (tmp_path / "parkinson-disease" / "codex_backbone.json").exists()


def test_codex_backbone_agent_enabled_runs_mocked_provider(tmp_path: Path) -> None:
    provider = FakeCodexProvider()
    context = _context(tmp_path, enable=True)

    result = CodexBackboneAgent(provider=provider).run(context)

    assert [task.metadata["requested_task"] for task in provider.tasks] == [
        "summarize_run",
        "explain_top_candidates",
        "draft_review_questions",
        "plan_followup_run",
    ]
    assert len(result.config["codex_backbone_results"]) == 4
    output_path = tmp_path / "parkinson-disease" / "codex_backbone.json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    assert payload["summary"]["succeeded_count"] == 4
    assert result.traces[-1].metadata["statuses"]["codex-summarize-run"] == "succeeded"


def test_codex_output_stored_separately_from_evidence_and_scores(tmp_path: Path) -> None:
    context = _context(tmp_path, enable=True)
    original_score = context.candidates[0].score
    original_evidence = list(context.candidates[0].evidence)

    result = CodexBackboneAgent(provider=FakeCodexProvider()).run(context)

    assert result.candidates[0].score == original_score
    assert result.candidates[0].evidence == original_evidence
    assert "codex_backbone_results" in result.config
    assert all(item.source != "Codex" for item in result.candidates[0].evidence)


def test_guardrail_failure_warns_and_continues_by_default(tmp_path: Path) -> None:
    context = _context(tmp_path, enable=True)

    result = CodexBackboneAgent(provider=FakeCodexProvider(["guardrail_failed"])).run(context)

    assert result.config["codex_backbone_summary"]["failed_count"] == 1
    assert result.traces[-1].metadata["guardrail_warnings"] == ["blocked unsupported claim"]


def test_strict_codex_backbone_failure_raises(tmp_path: Path) -> None:
    context = _context(tmp_path, enable=True, strict=True)

    with pytest.raises(AgentExecutionError, match="Codex backbone failed in strict mode"):
        CodexBackboneAgent(provider=FakeCodexProvider(["guardrail_failed"])).run(context)


def _context(tmp_path: Path, *, enable: bool, strict: bool = False) -> PipelineContext:
    disease = Disease(
        input_name="PD",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[],
    )
    evidence = EvidenceItem(
        source="ChEMBL",
        source_record_id="mec-1",
        title="Mechanism record",
        evidence_type="mechanism",
        summary="Mocked ChEMBL mechanism evidence.",
        confidence=0.8,
    )
    score = ScoreBreakdown(
        disease_target_relevance=0.8,
        molecule_target_evidence=0.7,
        mechanism_plausibility=0.6,
        clinical_precedence=0.5,
        safety_prior=0.7,
        data_quality=0.8,
        novelty_or_repurposing_value=0.4,
        final_score=0.72,
        confidence=0.75,
        explanation="Transparent scoring explanation.",
    )
    candidate = MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        evidence=[evidence],
        score=score.final_score,
        score_breakdown=score,
    )
    return PipelineContext(
        disease_input="PD",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        config={
            "results_dir": str(tmp_path),
            "enable_codex_backbone": enable,
            "strict_codex_backbone": strict,
            "codex_tasks": [
                "summarize_run",
                "explain_top_candidates",
                "draft_review_questions",
                "plan_followup_run",
            ],
            "codex_store_transcripts": False,
            "codex_max_tasks_per_run": 5,
        },
    )
