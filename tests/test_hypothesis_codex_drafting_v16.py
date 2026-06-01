from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.hypotheses.codex_drafting import CodexHypothesisDrafter
from molecule_ranker.hypotheses.schemas import ResearchHypothesis


class FakeDraftingProvider:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        output_text: str | None = None,
    ) -> None:
        self.payload = payload
        self.output_text = output_text
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        output_text = self.output_text
        if output_text is None:
            output_text = json.dumps(self.payload or {})
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=output_text,
            output_json=self.payload,
            stdout=output_text,
        )


def test_valid_codex_hypothesis_statement_draft_is_accepted(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "statement": (
                "Hypothesis for review: graph-backed MAOB context may help prioritize "
                "a high-level evidence review."
            ),
            "entity_ids": ["target:MAOB", "molecule:seed"],
            "relation_ids": ["rel:molecule-target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    draft = _drafter(provider, tmp_path).draft_hypothesis_statement(
        _candidate(),
        graph_paths=[["molecule:seed", "target:MAOB"]],
        evidence_summaries=["Source-backed molecule-target context is present."],
        contradiction_summaries=[],
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "accepted"
    assert draft.used_fallback is False
    assert draft.statement.startswith("Hypothesis for review")
    assert provider.tasks[0].task_type == "draft_hypothesis_statement"
    assert provider.tasks[0].expected_output_format == "json"
    assert provider.tasks[0].metadata["allowed_entity_ids"] == ["molecule:seed", "target:MAOB"]


def test_fake_entity_reference_is_rejected_and_falls_back(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "statement": "Hypothesis for review: target:FAKE should be considered.",
            "entity_ids": ["target:FAKE"],
            "relation_ids": ["rel:molecule-target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    draft = _drafter(provider, tmp_path).draft_hypothesis_statement(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "fallback"
    assert draft.used_fallback is True
    assert draft.statement == _candidate().statement
    assert any("unknown entity ID: target:FAKE" in warning for warning in draft.warnings)


def test_invented_assay_result_reference_is_rejected(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "questions": ["Would the graph conflict be clarified by high-level review?"],
            "entity_ids": ["target:MAOB", "assay_result:invented"],
            "relation_ids": ["rel:molecule-target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
            "assay_result_ids": ["assay_result:invented"],
        }
    )

    draft = _drafter(provider, tmp_path).draft_research_questions(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "fallback"
    assert draft.research_questions
    assert any("unknown entity ID: assay_result:invented" in warning for warning in draft.warnings)


def test_protocol_text_is_rejected(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "criteria": [
                "Incubate cells for 24 hours at 37 C with compound and compare signal."
            ],
            "entity_ids": ["target:MAOB"],
            "relation_ids": ["rel:molecule-target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    draft = _drafter(provider, tmp_path).draft_falsification_criteria(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "fallback"
    assert draft.used_fallback is True
    assert any("lab protocols" in warning for warning in draft.warnings)


def test_unsupported_activity_claim_is_rejected(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "limitations": ["The molecule binds target:MAOB and is active."],
            "entity_ids": ["target:MAOB"],
            "relation_ids": ["rel:molecule-target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    draft = _drafter(provider, tmp_path).draft_limitations(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "fallback"
    assert any("activity" in warning for warning in draft.warnings)


def test_fallback_used_when_codex_output_is_not_json(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(output_text="This is not JSON.")

    draft = _drafter(provider, tmp_path).draft_review_questions(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "fallback"
    assert draft.review_questions
    assert any("JSON" in warning for warning in draft.warnings)


def test_safe_hypothesis_explanation_passes_with_required_citations(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "explanation": (
                "The hypothesis cites graph-backed molecule-target context and remains "
                "a planning hypothesis, not evidence."
            ),
            "entity_ids": ["target:MAOB", "molecule:seed"],
            "relation_ids": ["rel:molecule-target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    draft = _drafter(provider, tmp_path).explain_hypothesis_evidence(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "accepted"
    assert draft.explanation.startswith("The hypothesis cites")
    assert provider.tasks[0].task_type == "explain_hypothesis_evidence"
    assert provider.tasks[0].metadata["allowed_provenance_ids"] == ["prov:kg"]


def test_missing_required_citation_fields_falls_back(tmp_path: Path) -> None:
    provider = FakeDraftingProvider(
        {
            "hypothesis_id": "hypothesis:maob",
            "explanation": "This explanation omits required provenance citations.",
            "entity_ids": ["target:MAOB"],
            "relation_ids": ["rel:molecule-target"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    draft = _drafter(provider, tmp_path).explain_hypothesis_evidence(
        _candidate(),
        allowed_entity_ids=["target:MAOB", "molecule:seed"],
        allowed_relation_ids=["rel:molecule-target"],
        allowed_provenance_ids=["prov:kg"],
        allowed_artifact_ids=["artifact:kg"],
    )

    assert draft.status == "fallback"
    assert any("provenance_ids" in warning for warning in draft.warnings)


def _drafter(provider: FakeDraftingProvider, tmp_path: Path) -> CodexHypothesisDrafter:
    return CodexHypothesisDrafter(provider, working_directory=tmp_path)


def _candidate() -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id="hypothesis:maob",
        hypothesis_type="molecule_target",
        title="Hypothesis: MAOB molecule-target review",
        statement=(
            "Hypothesis for review: graph-backed molecule-target context requires "
            "high-level validation planning."
        ),
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:seed"],
        supporting_relation_ids=["rel:molecule-target"],
        source_artifact_ids=["artifact:kg"],
        support_score=0.8,
        uncertainty_score=0.5,
        priority_score=0.6,
        confidence=0.7,
        metadata={"pattern": "supported_mechanism_expansion"},
    )
