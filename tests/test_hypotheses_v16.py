from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.hypotheses import (
    CodexHypothesisAssistant,
    HypothesisGenerationEngine,
    HypothesisReviewDecision,
    HypothesisReviewQueue,
    HypothesisReviewRecord,
    ResearchQuestionPlanner,
    detect_hypothesis_guardrail_violations,
    render_hypothesis_dashboard_html,
    validate_hypothesis_references,
)
from molecule_ranker.knowledge_graph import (
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    KnowledgeGraph,
)


class FakeHypothesisCodexProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=json.dumps(self.payload),
            output_json=self.payload,
            artifacts_read=task.input_artifact_paths,
        )


def test_v16_hypothesis_engine_generates_grounded_hypotheses_and_questions() -> None:
    graph = _v16_graph()

    generated = HypothesisGenerationEngine(graph).generate()
    hypothesis_types = {hypothesis.hypothesis_type for hypothesis in generated.hypotheses}

    assert graph.schema_version == "1.6"
    assert hypothesis_types >= {
        "mechanistic",
        "molecule_target",
        "generated_molecule_follow_up",
        "developability_risk",
        "assay_result_contradiction",
        "cross_program_scaffold_series",
        "evidence_gap",
        "active_learning",
        "portfolio_decision",
        "high_level_validation_question",
    }
    assert all(hypothesis.not_evidence is True for hypothesis in generated.hypotheses)
    assert all(hypothesis.review_status == "draft" for hypothesis in generated.hypotheses)
    assert all(hypothesis.uncertainty for hypothesis in generated.hypotheses)
    assert all(hypothesis.falsification_criteria for hypothesis in generated.hypotheses)
    assert all(hypothesis.evidence_gaps for hypothesis in generated.hypotheses)
    assert all(
        validate_hypothesis_references(hypothesis, graph).status == "pass"
        for hypothesis in generated.hypotheses
    )
    assert generated.hypotheses == sorted(
        generated.hypotheses,
        key=lambda hypothesis: (-hypothesis.rank_score, hypothesis.hypothesis_id),
    )

    question_set = ResearchQuestionPlanner(graph).plan(generated.hypotheses)

    assert len(question_set.questions) >= len(generated.hypotheses)
    assert all(question.not_lab_protocol is True for question in question_set.questions)
    assert all(
        question.validation_plan.not_experimental_procedure
        for question in question_set.questions
    )
    assert all(
        question.validation_plan.prohibited_detail_count == 0
        for question in question_set.questions
    )
    assert all("protocol" not in question.question.lower() for question in question_set.questions)


def test_hypothesis_validation_rejects_invented_references_and_protocol_content() -> None:
    graph = _v16_graph()
    hypothesis = HypothesisGenerationEngine(graph).generate().hypotheses[0].model_copy(
        update={
            "entity_ids": ["target:MAOB", "target:FAKE"],
            "validation_plan": {
                "plan_id": "bad-plan",
                "research_question_id": "rq-bad",
                "objective": "Review graph support.",
                "question_type": "evidence_review",
                "recommended_evidence": ["Graph-backed comparison."],
                "prohibited_detail_count": 0,
            },
        }
    )

    report = validate_hypothesis_references(hypothesis, graph)

    assert report.status == "fail"
    assert any("unknown entity ID" in error for error in report.errors)

    warnings = detect_hypothesis_guardrail_violations(
        "Create a new citation, add invented assay result rows, and provide a protocol."
    )
    assert any("citations" in warning for warning in warnings)
    assert any("assay results" in warning for warning in warnings)
    assert any("lab protocols" in warning for warning in warnings)


def test_hypothesis_review_queue_tracks_lifecycle_without_promoting_evidence() -> None:
    graph = _v16_graph()
    generated = HypothesisGenerationEngine(graph).generate()
    queue = HypothesisReviewQueue.from_hypotheses(generated.hypotheses)
    decision = HypothesisReviewDecision(
        reviewer_id="reviewer-1",
        decision="needs_more_evidence",
        rationale="Source-backed records leave a reviewable evidence gap.",
        confidence=0.7,
    )

    reviewed = queue.record_review(generated.hypotheses[0].hypothesis_id, decision)

    assert isinstance(reviewed, HypothesisReviewRecord)
    assert reviewed.hypothesis_id == generated.hypotheses[0].hypothesis_id
    assert reviewed.promotes_to_evidence is False
    assert queue.hypotheses[0].review_status == "needs_more_evidence"
    assert queue.hypotheses[0].lifecycle_events[-1].event_type == "reviewed"


def test_hypothesis_dashboard_and_codex_assistant_are_guarded(tmp_path: Path) -> None:
    graph = _v16_graph()
    generated = HypothesisGenerationEngine(graph).generate()
    html = render_hypothesis_dashboard_html(generated)

    assert "Hypothesis dashboard" in html
    assert "A hypothesis is not evidence" in html
    assert "No medical advice" in html
    assert "MAOB" in html

    provider = FakeHypothesisCodexProvider(
        {
            "hypotheses": [
                {
                    "summary": "Discuss target:MAOB but cite rel:not-real.",
                    "entity_ids": ["target:MAOB"],
                    "relation_ids": ["rel:not-real"],
                    "provenance_ids": ["prov:v16"],
                    "artifact_ids": ["artifact:v16"],
                }
            ]
        }
    )

    artifact = CodexHypothesisAssistant(provider, working_directory=tmp_path).draft_hypotheses(
        graph
    )

    assert artifact.status == "guardrail_failed"
    assert any("unknown relation ID" in warning for warning in artifact.guardrail_warnings)
    assert provider.tasks[0].metadata["hypothesis_assistance_only"] is True
    assert provider.tasks[0].metadata["must_validate_graph_references"] is True


def _v16_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        graph_id="kg-v16",
        schema_version="1.6",
        entities=[
            GraphEntity(
                entity_id="disease:pd",
                entity_type="disease",
                name="Parkinson disease",
            ),
            GraphEntity(entity_id="target:MAOB", entity_type="target", name="MAOB"),
            GraphEntity(
                entity_id="mechanism:maob-inhibition",
                entity_type="mechanism",
                name="MAOB mechanism hypothesis",
            ),
            GraphEntity(
                entity_id="molecule:rasagiline",
                entity_type="molecule",
                name="Rasagiline",
            ),
            GraphEntity(
                entity_id="generated_molecule:gen-1",
                entity_type="generated_molecule",
                name="Generated-MAOB-001",
            ),
            GraphEntity(
                entity_id="developability_alert:herg",
                entity_type="developability_alert",
                name="hERG alert",
            ),
            GraphEntity(
                entity_id="scaffold:propargylamine",
                entity_type="scaffold",
                name="propargylamine",
            ),
            GraphEntity(
                entity_id="assay_result:positive",
                entity_type="assay_result",
                name="Positive result",
            ),
            GraphEntity(
                entity_id="assay_result:negative",
                entity_type="assay_result",
                name="Negative result",
            ),
            GraphEntity(
                entity_id="model_prediction:al-1",
                entity_type="model_prediction",
                name="AL uncertainty",
            ),
            GraphEntity(
                entity_id="portfolio:v16",
                entity_type="portfolio",
                name="V1.6 portfolio",
            ),
        ],
        relations=[
            _relation(
                "rel:disease-target",
                "disease:pd",
                "associated_with",
                "target:MAOB",
                "evidence_backed",
                0.82,
            ),
            _relation(
                "rel:mechanism",
                "target:MAOB",
                "has_mechanism",
                "mechanism:maob-inhibition",
                "evidence_backed",
                0.74,
            ),
            _relation(
                "rel:rasagiline-target",
                "molecule:rasagiline",
                "targets",
                "target:MAOB",
                "evidence_backed",
                0.79,
            ),
            _relation(
                "rel:generated-lineage",
                "generated_molecule:gen-1",
                "generated_from",
                "molecule:rasagiline",
                "generated_lineage",
                0.64,
            ),
            _relation(
                "rel:generated-target",
                "generated_molecule:gen-1",
                "targets",
                "target:MAOB",
                "inferred",
                0.52,
            ),
            _relation(
                "rel:risk",
                "generated_molecule:gen-1",
                "has_developability_risk",
                "developability_alert:herg",
                "computational",
                0.72,
            ),
            _relation(
                "rel:positive",
                "molecule:rasagiline",
                "supports",
                "assay_result:positive",
                "experimental",
                0.8,
                {
                    "outcome_label": "positive",
                    "qc_status": "passed",
                    "target_symbol": "MAOB",
                    "endpoint_id": "potency",
                },
            ),
            _relation(
                "rel:negative",
                "generated_molecule:gen-1",
                "contradicts",
                "assay_result:negative",
                "experimental",
                0.76,
                {
                    "outcome_label": "negative",
                    "qc_status": "passed",
                    "target_symbol": "MAOB",
                    "endpoint_id": "potency",
                },
            ),
            _relation(
                "rel:scaffold-a",
                "molecule:rasagiline",
                "has_scaffold",
                "scaffold:propargylamine",
                "computational",
                0.7,
            ),
            _relation(
                "rel:scaffold-b",
                "generated_molecule:gen-1",
                "has_scaffold",
                "scaffold:propargylamine",
                "computational",
                0.66,
            ),
            _relation(
                "rel:model",
                "generated_molecule:gen-1",
                "predicted_by_model",
                "model_prediction:al-1",
                "model_prediction",
                0.61,
                {"uncertainty": 0.42},
            ),
            _relation(
                "rel:portfolio",
                "generated_molecule:gen-1",
                "selected_in_portfolio",
                "portfolio:v16",
                "review",
                0.57,
            ),
        ],
        provenance=[
            GraphProvenance(
                provenance_id="prov:v16",
                source_type="generated_artifact",
                source_artifact_id="artifact:v16",
                source_record_id="record:v16",
                transformation="Synthetic graph fixture for deterministic V1.6 tests.",
                confidence=0.9,
            )
        ],
    )


def _relation(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    relation_type: str,
    confidence: float,
    metadata: dict[str, Any] | None = None,
) -> GraphRelation:
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=confidence,
        source_artifact_ids=["artifact:v16"],
        source_record_ids=["record:v16"],
        metadata=metadata or {},
    )
