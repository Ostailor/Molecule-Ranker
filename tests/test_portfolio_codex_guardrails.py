from __future__ import annotations

import json
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


def test_portfolio_codex_fake_metric_flagged(tmp_path: Path) -> None:
    artifact = _portfolio_artifact(tmp_path)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "draft_decision_memo",
        {
            "optimization_run_id": "opt-1",
            "selection_id": "sel-1",
            "candidate_ids": ["cand-a"],
            "artifact_ids": ["artifact-opt"],
            "memo_sections": ["portfolio_score: 0.99"],
        },
    )

    guarded = check_output(result, refs, citations)

    assert guarded.status == "guardrail_failed"
    assert any("Unbacked portfolio metric" in warning for warning in guarded.guardrail_warnings)


def test_portfolio_codex_stage_gate_approval_attempt_rejected(tmp_path: Path) -> None:
    artifact = _portfolio_artifact(tmp_path)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "draft_decision_memo",
        {
            "optimization_run_id": "opt-1",
            "selection_id": "sel-1",
            "candidate_ids": ["cand-a"],
            "artifact_ids": ["artifact-opt"],
            "approved": True,
        },
    )

    guarded = check_output(result, refs, citations)

    assert guarded.status == "guardrail_failed"
    assert any(
        "Forbidden portfolio Codex action" in warning
        for warning in guarded.guardrail_warnings
    )


def test_portfolio_codex_ungrounded_candidate_id_flagged(tmp_path: Path) -> None:
    artifact = _portfolio_artifact(tmp_path)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "explain_candidate_rejection",
        {
            "optimization_run_id": "opt-1",
            "selection_id": "sel-1",
            "candidate_ids": ["cand-z"],
            "artifact_ids": ["artifact-opt"],
            "rejection_explanation": "candidate_id: cand-z was deferred.",
        },
    )

    guarded = check_output(result, refs, citations)

    assert guarded.status == "guardrail_failed"
    assert any(
        "Ungrounded portfolio candidate ID" in warning
        for warning in guarded.guardrail_warnings
    )


def test_safe_portfolio_memo_passes_guardrails(tmp_path: Path) -> None:
    artifact = _portfolio_artifact(tmp_path)
    refs, citations = collect_allowed_refs_from_artifacts([str(artifact)])
    result = _result(
        "draft_decision_memo",
        {
            "status": "draft",
            "optimization_run_id": "opt-1",
            "selection_id": "sel-1",
            "candidate_ids": ["cand-a"],
            "artifact_ids": ["artifact-opt"],
            "memo_sections": [
                "Selection sel-1 from optimization_run_id opt-1 prioritizes candidate_id: "
                "cand-a as an advisory research portfolio item from artifact-opt."
            ],
            "limitations": ["No activity, safety, efficacy, or synthesis claim is made."],
        },
    )

    guarded = check_output(result, refs, citations)

    assert guarded.status == "succeeded"
    assert guarded.guardrail_warnings == []


def test_portfolio_prompt_template_limits_codex_to_explanations(tmp_path: Path) -> None:
    artifact = _portfolio_artifact(tmp_path)
    task = CodexTask(
        task_id="task-portfolio",
        task_type="summarize_portfolio_tradeoffs",
        prompt="Summarize tradeoffs from deterministic output.",
        working_directory=str(tmp_path),
        input_artifact_paths=[str(artifact)],
    )

    payload = json.loads(build_codex_prompt(task, CodexBackboneConfig()).prompt_text)
    instructions = " ".join(payload["instructions"])

    assert "Codex is limited to explanation" in instructions
    assert "Codex cannot select a portfolio" in instructions
    assert "Codex cannot approve stage gates" in instructions
    assert "optimization_run_id" in instructions


def _portfolio_artifact(tmp_path: Path) -> Path:
    path = tmp_path / "portfolio_optimization.json"
    path.write_text(
        json.dumps(
            {
                "artifact_id": "artifact-opt",
                "artifact_ids": ["artifact-opt"],
                "optimization_run_id": "opt-1",
                "selections": [
                    {
                        "selection_id": "sel-1",
                        "selected_candidate_ids": ["cand-a"],
                        "rejected_candidate_ids": ["cand-risk"],
                        "objective_scores": {"portfolio_score": 0.71},
                        "portfolio_score": 0.71,
                    }
                ],
                "scenario_ids": ["scenario-conservative"],
            }
        )
    )
    return path


def _result(task_type: str, payload: dict[str, object]) -> CodexTaskResult:
    return CodexTaskResult(
        task_id="task-1",
        task_type=task_type,  # type: ignore[arg-type]
        status="succeeded",
        output_text=json.dumps(payload),
        output_json=payload,
    )
