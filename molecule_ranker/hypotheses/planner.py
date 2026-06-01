from __future__ import annotations

from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph

from .schemas import Hypothesis, ResearchQuestion, ResearchQuestionSet, ValidationPlan


class ResearchQuestionPlanner:
    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph

    def plan(self, hypotheses: list[Hypothesis]) -> ResearchQuestionSet:
        questions = [self._question(hypothesis) for hypothesis in hypotheses]
        return ResearchQuestionSet(graph_id=self.graph.graph_id, questions=questions)

    def _question(self, hypothesis: Hypothesis) -> ResearchQuestion:
        question = (
            f"What graph-backed evidence, contradictions, and gaps should reviewers consider "
            f"for {hypothesis.title.lower()}?"
        )
        plan = hypothesis.validation_plan or ValidationPlan(
            research_question_id=f"rq-{hypothesis.hypothesis_id}",
            objective="Review cited graph records without defining experimental procedures.",
            question_type="evidence_review",
            recommended_evidence=[
                "Inspect cited entity IDs, relation IDs, provenance IDs, and artifact IDs."
            ],
        )
        return ResearchQuestion(
            question_id=plan.research_question_id,
            hypothesis_id=hypothesis.hypothesis_id,
            question_type=plan.question_type,
            question=question,
            entity_ids=hypothesis.entity_ids,
            relation_ids=hypothesis.relation_ids,
            validation_plan=plan,
            review_status=hypothesis.review_status,
        )
