from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.codex_backbone.guardrails import (
    check_output,
    collect_allowed_refs_from_artifacts,
)
from molecule_ranker.codex_backbone.prompts import build_codex_prompt
from molecule_ranker.codex_backbone.schemas import (
    CodexBackboneConfig,
    CodexTask,
    CodexTaskResult,
)
from molecule_ranker.evaluation.codex_explanations import (
    EVALUATION_CODEX_TASK_TYPES,
    validate_evaluation_codex_output,
)


def test_evaluation_codex_fake_metric_flagged(tmp_path: Path) -> None:
    artifact = _evaluation_artifact(tmp_path)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "summarize_evaluation_report",
        {
            "status": "ok",
            "summary": "Evaluation eval-1 used fabricated metric fake_metric.",
            "evaluation_id": "eval-1",
            "task_id": "task-1",
            "dataset_id": "dataset-1",
            "split_id": "split-1",
            "metric_ids": ["precision-at-1", "fake_metric"],
            "artifact_ids": ["evaluation-report-artifact"],
            "limitations": ["Benchmark results are evaluation artifacts."],
        },
    )

    checked = check_output(result, refs, citations)

    assert checked.status == "guardrail_failed"
    assert any(
        "Unbacked evaluation metric ID: fake_metric" in warning
        for warning in checked.guardrail_warnings
    )


def test_evaluation_codex_hidden_guardrail_failure_flagged(tmp_path: Path) -> None:
    artifact = _evaluation_artifact(tmp_path, include_guardrail_failure=True)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "explain_guardrail_failures",
        {
            "status": "ok",
            "summary": "No guardrail failures were detected for evaluation eval-1.",
            "evaluation_id": "eval-1",
            "task_id": "task-1",
            "dataset_id": "dataset-1",
            "split_id": "split-1",
            "metric_ids": ["precision-at-1", "guardrail-pass-rate"],
            "artifact_ids": ["evaluation-report-artifact"],
            "limitations": ["Benchmark results are evaluation artifacts."],
        },
    )

    checked = check_output(result, refs, citations)

    assert checked.status == "guardrail_failed"
    assert any(
        "hid a recorded guardrail failure" in warning
        for warning in checked.guardrail_warnings
    )


def test_safe_evaluation_summary_passes(tmp_path: Path) -> None:
    artifact = _evaluation_artifact(tmp_path, include_guardrail_failure=True)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "summarize_evaluation_report",
        {
            "status": "ok",
            "summary": (
                "Evaluation eval-1 for task task-1 used dataset dataset-1 and split split-1. "
                "Metrics cited are precision-at-1 and guardrail-pass-rate from "
                "evaluation-report-artifact. Guardrail failures are surfaced for review."
            ),
            "evaluation_id": "eval-1",
            "task_id": "task-1",
            "dataset_id": "dataset-1",
            "split_id": "split-1",
            "metric_ids": ["precision-at-1", "guardrail-pass-rate"],
            "artifact_ids": ["evaluation-report-artifact"],
            "limitations": [
                "Benchmark results are evaluation artifacts.",
                "This is not clinical validation or proof of efficacy, safety, activity, "
                "or synthesizability.",
            ],
        },
    )

    checked = check_output(result, refs, citations)

    assert checked.status == "succeeded"
    assert checked.guardrail_warnings == []


def test_evaluation_codex_prompt_template_limits_tasks_to_explanation(tmp_path: Path) -> None:
    artifact = _evaluation_artifact(tmp_path)
    task = CodexTask(
        task_id="codex-eval-task",
        task_type="draft_benchmark_limitations",
        prompt="Draft benchmark limitations from the supplied evaluation report.",
        working_directory=str(tmp_path),
        input_artifact_paths=[str(artifact)],
    )

    payload = json.loads(build_codex_prompt(task, CodexBackboneConfig()).prompt_text)
    instructions = " ".join(payload["instructions"])

    assert EVALUATION_CODEX_TASK_TYPES == {
        "summarize_evaluation_report",
        "explain_metric_changes",
        "draft_benchmark_limitations",
        "summarize_prospective_validation",
        "explain_guardrail_failures",
        "draft_decision_quality_lessons",
    }
    assert "Codex is limited to evaluation explanation" in instructions
    assert "Codex cannot invent metrics" in instructions
    assert "Codex cannot hide guardrail failures" in instructions
    assert "evaluation_id" in instructions


def test_direct_evaluation_validator_reports_missing_required_citations() -> None:
    checked = validate_evaluation_codex_output(
        _result(
            "draft_decision_quality_lessons",
            {
                "status": "ok",
                "summary": "A cautious summary that omits metric IDs.",
                "evaluation_id": "eval-1",
                "task_id": "task-1",
                "dataset_id": "dataset-1",
                "split_id": "split-1",
                "artifact_ids": ["evaluation-report-artifact"],
                "limitations": ["Benchmark results are evaluation artifacts."],
            },
        ),
        allowed_artifact_refs={
            "evaluation_id:eval-1",
            "task_id:task-1",
            "dataset_id:dataset-1",
            "split_id:split-1",
            "artifact_id:evaluation-report-artifact",
            "metric_id:precision-at-1",
        },
    )

    assert checked.status == "guardrail_failed"
    assert any("missing required metric IDs" in warning for warning in checked.guardrail_warnings)


def _evaluation_artifact(tmp_path: Path, *, include_guardrail_failure: bool = False) -> Path:
    path = tmp_path / "evaluation_report.json"
    path.write_text(
        json.dumps(
            {
                "artifact_id": "evaluation-report-artifact",
                "artifact_ids": ["evaluation-report-artifact"],
                "evaluation_id": "eval-1",
                "task_id": "task-1",
                "dataset_id": "dataset-1",
                "split_id": "split-1",
                "metrics": [
                    {
                        "metric_id": "precision-at-1",
                        "name": "precision_at_1",
                        "metric_type": "ranking",
                        "value": 0.5,
                        "higher_is_better": True,
                    },
                    {
                        "metric_id": "guardrail-pass-rate",
                        "name": "guardrail_pass_rate",
                        "metric_type": "guardrail",
                        "value": False if include_guardrail_failure else True,
                        "higher_is_better": True,
                    },
                ],
                "warnings": (
                    ["guardrail_benchmark_failure: fake_result_case failed"]
                    if include_guardrail_failure
                    else []
                ),
                "limitations": ["Benchmark results are evaluation artifacts."],
                "created_at": datetime(2026, 1, 1, 12, tzinfo=UTC).isoformat(),
            },
            sort_keys=True,
        )
    )
    return path


def _result(task_type: str, payload: dict[str, object]) -> CodexTaskResult:
    return CodexTaskResult(
        task_id="task-1",
        task_type=task_type,  # type: ignore[arg-type]
        status="succeeded",
        output_text=json.dumps(payload, sort_keys=True),
        output_json=payload,
    )
