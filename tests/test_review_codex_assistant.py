from __future__ import annotations

import json
from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.review.codex_assistant import CodexReviewAssistant
from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace
from molecule_ranker.review.workspace import ReviewWorkspaceStore


class FakeCodexProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        output = json.dumps(self.payload)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=output,
            output_json=self.payload,
            stdout=output,
            return_code=0,
        )


def test_codex_review_questions_stored_separately_from_decisions_and_evidence(tmp_path):
    workspace = _workspace()
    original_score = workspace.review_items[0].score
    original_evidence = dict(workspace.review_items[0].evidence_summary)
    store = ReviewWorkspaceStore(tmp_path / "review.sqlite")
    store.create_workspace(workspace)
    provider = FakeCodexProvider(
        {
            "candidate_name": "Rasagiline",
            "review_questions": ["Which artifact-backed uncertainty should be reviewed?"],
            "uncertainty_questions": ["Which evidence gap most affects triage?"],
            "not_claimed": ["No clinical conclusion is made."],
            "artifact_refs": ["review-context"],
        }
    )

    artifact = CodexReviewAssistant(provider, working_directory=tmp_path).draft_questions(
        workspace,
        "item-rasagiline",
    )
    store.add_codex_review_artifact(workspace.workspace_id, artifact)
    reloaded = store.get_workspace(workspace.workspace_id)

    assert len(reloaded.codex_review_artifacts) == 1
    assert reloaded.decisions == []
    assert reloaded.review_items[0].score == original_score
    assert reloaded.review_items[0].evidence_summary == original_evidence
    assert reloaded.codex_review_artifacts[0].task_type == "codex_review_questions"


def test_codex_review_questions_withhold_protocol_dosing_or_synthesis_content(tmp_path):
    workspace = _workspace()
    provider = FakeCodexProvider(
        {
            "candidate_name": "Rasagiline",
            "review_questions": [
                "What synthesis route should be used?",
                "What human dosing should be selected?",
            ],
            "artifact_refs": ["review-context"],
        }
    )

    artifact = CodexReviewAssistant(provider, working_directory=tmp_path).draft_questions(
        workspace,
        "item-rasagiline",
    )
    dumped = json.dumps(artifact.output_json).lower()

    assert artifact.status == "guardrail_failed"
    assert "synthesis route should be used" not in dumped
    assert "human dosing should be selected" not in dumped
    assert artifact.guardrail_warnings


def test_codex_generated_molecule_summary_preserves_no_direct_evidence_warning(tmp_path):
    workspace = _workspace(generated=True)
    provider = FakeCodexProvider(
        {
            "executive_summary": "Generated candidate summary from review artifacts.",
            "key_evidence": ["Generation metadata only."],
            "key_risks": ["Evidence is limited."],
            "validation_questions": ["Which non-operational checks are needed?"],
            "artifact_refs": ["review-context"],
        }
    )

    artifact = CodexReviewAssistant(provider, working_directory=tmp_path).summarize_dossier(
        workspace,
        "item-generated",
    )

    assert artifact.output_json is not None
    not_claimed = artifact.output_json["not_claimed"]
    assert any("no direct experimental evidence" in item.lower() for item in not_claimed)
    assert artifact.review_item_ids == ["item-generated"]


def _workspace(*, generated: bool = False) -> ReviewWorkspace:
    item = (
        ReviewItem(
            review_item_id="item-generated",
            run_id="run-1",
            disease_name="Parkinson disease",
            candidate_id="generated-1",
            candidate_name="Generated-MAOB-001",
            candidate_origin="generated",
            target_symbols=["MAOB"],
            canonical_smiles="CCOC1=CC=CC=C1",
            score=0.62,
            confidence=0.41,
            evidence_summary={"score_breakdown": {"final_score": 0.62}},
            generation_summary={"source": "generated_candidates.json"},
            warnings=["Generated hypothesis; no direct activity evidence."],
            priority_bucket="needs_review",
            review_status="pending",
        )
        if generated
        else ReviewItem(
            review_item_id="item-rasagiline",
            run_id="run-1",
            disease_name="Parkinson disease",
            candidate_id="chembl-rasagiline",
            candidate_name="Rasagiline",
            candidate_origin="existing",
            target_symbols=["MAOB"],
            canonical_smiles="CNCCC1=CC=CC=C1",
            score=0.82,
            confidence=0.76,
            evidence_summary={"score_breakdown": {"final_score": 0.82}},
            literature_summary={"citations": [{"pmid": "123456"}]},
            developability_summary={"risk_level": "low"},
            warnings=["Research triage only."],
            priority_bucket="high_priority",
            review_status="pending",
        )
    )
    return ReviewWorkspace(
        workspace_id="workspace-review-codex",
        run_id="run-1",
        disease_name="Parkinson disease",
        review_items=[item],
    )
