from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.hypotheses.codex_drafting import CodexHypothesisDrafter
from molecule_ranker.hypotheses.evidence_gap import analyze_evidence_gaps_for_hypotheses
from molecule_ranker.hypotheses.falsification import build_falsification_criteria
from molecule_ranker.hypotheses.generator import generate_hypothesis_candidates
from molecule_ranker.hypotheses.questions import plan_research_questions
from molecule_ranker.hypotheses.ranking import rank_research_hypotheses
from molecule_ranker.hypotheses.reports import render_hypothesis_report_markdown
from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    HypothesisGenerationRun,
    ResearchHypothesis,
    TestableResearchQuestion,
)
from molecule_ranker.hypotheses.store import HypothesisStore
from molecule_ranker.hypotheses.validation import (
    allowed_hypothesis_reference_sets,
    detect_hypothesis_guardrail_violations,
)
from molecule_ranker.knowledge_graph.builder import GraphBuilder
from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph
from molecule_ranker.knowledge_graph.store import KnowledgeGraphStore
from molecule_ranker.schemas import RankingRun
from molecule_ranker.utils import slugify


class HypothesisGenerationAgent(BaseAgent):
    """Run V1.6 deterministic hypothesis generation before portfolio/review/reporting."""

    name = "HypothesisGenerationAgent"

    def __init__(self) -> None:
        super().__init__()
        self._last_metadata: dict[str, Any] = self._disabled_metadata()

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_hypothesis_generation", False)):
            self._last_metadata = self._disabled_metadata()
            return context

        output_dir = _hypothesis_output_dir(context)
        output_dir.mkdir(parents=True, exist_ok=True)
        graph = _load_or_build_graph(context)
        graph_store = KnowledgeGraphStore(output_dir)
        graph_store.save(graph, actor=self.name, reason="hypothesis_generation")

        max_hypotheses = _positive_int(context.config, "max_hypotheses", 100)
        candidates = generate_hypothesis_candidates(
            graph_store,
            mechanism_hypotheses=graph.mechanisms,
            contradiction_reports=context.config.get("contradiction_reports") or [],
            staleness_reports=context.config.get("staleness_reports") or [],
            portfolio_selections=_portfolio_selections(context),
            assay_result_summaries=context.config.get("assay_result_summaries") or [],
            model_predictions=context.config.get("model_predictions") or [],
            structure_assessments=context.config.get("structure_assessments") or [],
            review_decisions=context.config.get("review_decisions") or [],
        )[:max_hypotheses]
        hypotheses, codex_metadata = _maybe_apply_codex_drafting(
            candidates,
            graph=graph,
            context=context,
            output_dir=output_dir,
        )
        _validate_hypotheses(hypotheses, graph, strict=_strict_guardrails(context))
        evidence_gaps_by_id = analyze_evidence_gaps_for_hypotheses(hypotheses, graph=graph)
        criteria_by_id = {
            hypothesis.hypothesis_id: build_falsification_criteria(hypothesis)
            for hypothesis in hypotheses
        }
        ranked = rank_research_hypotheses(
            hypotheses,
            evidence_gaps_by_hypothesis=evidence_gaps_by_id,
        )
        questions_by_id = {
            hypothesis.hypothesis_id: plan_research_questions(
                hypothesis,
                evidence_gaps=evidence_gaps_by_id.get(hypothesis.hypothesis_id, []),
                criteria=criteria_by_id.get(hypothesis.hypothesis_id, []),
            )[: _positive_int(context.config, "max_questions_per_hypothesis", 5)]
            for hypothesis in ranked
        }
        run = HypothesisGenerationRun(
            generation_run_id=f"hypothesis-run-{slugify(graph.graph_id)}",
            project_id=_optional_str(context.config.get("project_id")),
            program_id=_optional_str(context.config.get("program_id")),
            graph_build_id=_graph_build_id(graph),
            input_artifact_ids=_graph_artifact_ids(graph),
            hypothesis_count=len(ranked),
            accepted_count=len(ranked),
            rejected_count=0,
            warnings=[],
            completed_at=datetime.now(UTC),
            metadata={
                "graph_id": graph.graph_id,
                "codex_drafting": codex_metadata,
                "deterministic_validation": True,
                "ranking_is_for_research_planning_not_proof": True,
            },
        )
        store_path = _hypothesis_store_path(context, output_dir)
        _persist_hypothesis_records(
            store_path,
            hypotheses=ranked,
            evidence_gaps_by_id=evidence_gaps_by_id,
            criteria_by_id=criteria_by_id,
            questions_by_id=questions_by_id,
            run=run,
        )
        artifact_paths = _write_artifacts(
            output_dir=output_dir,
            store_path=store_path,
            graph=graph,
            hypotheses=ranked,
            evidence_gaps_by_id=evidence_gaps_by_id,
            criteria_by_id=criteria_by_id,
            questions_by_id=questions_by_id,
            run=run,
        )
        context.output_dir = output_dir
        context.config["hypothesis_generation"] = {
            "enabled": True,
            "graph_id": graph.graph_id,
            "hypothesis_count": len(ranked),
            "research_question_count": sum(len(items) for items in questions_by_id.values()),
            "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
            "store_path": str(store_path),
            "codex_drafting": codex_metadata,
            "generated_hypothesis_ids_requiring_review": [
                hypothesis.hypothesis_id
                for hypothesis in ranked
                if hypothesis.hypothesis_type == "generated_molecule"
                and hypothesis.status == "under_review"
            ],
        }
        self._last_metadata = {
            "enabled": True,
            "graph_id": graph.graph_id,
            "hypothesis_count": len(ranked),
            "research_question_count": sum(len(items) for items in questions_by_id.values()),
            "codex_enabled": codex_metadata["enabled"],
            "codex_fallback_count": codex_metadata["fallback_count"],
            "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
            "store_path": str(store_path),
        }
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        del context
        if not self._last_metadata.get("enabled", False):
            return "Hypothesis generation disabled."
        return (
            "Generated "
            f"{self._last_metadata.get('hypothesis_count', 0)} hypothesis candidate(s) "
            "for research planning."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        del context
        return dict(self._last_metadata)

    def _disabled_metadata(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "hypothesis_count": 0,
            "research_question_count": 0,
            "reason": "enable_hypothesis_generation is false",
        }


def _load_or_build_graph(context: PipelineContext) -> KnowledgeGraph:
    raw_graph = context.config.get("knowledge_graph")
    if isinstance(raw_graph, KnowledgeGraph):
        return raw_graph
    if isinstance(raw_graph, dict):
        return KnowledgeGraph.model_validate(raw_graph)
    graph_path = context.config.get("knowledge_graph_path")
    if graph_path:
        return KnowledgeGraph.model_validate(
            json.loads(Path(str(graph_path)).read_text(encoding="utf-8"))
        )
    artifact_dir = _artifact_dir(context)
    for candidate in [artifact_dir / "knowledge_graph.json", artifact_dir / "graph.json"]:
        if candidate.exists():
            return KnowledgeGraph.model_validate(json.loads(candidate.read_text(encoding="utf-8")))
    graph_id = str(
        context.config.get("knowledge_graph_id")
        or f"graph-{slugify(context.disease_input)}"
    )
    ranking_runs = []
    if context.disease is not None:
        ranking_runs.append(
            RankingRun(
                disease=context.disease,
                targets=context.targets,
                candidates=context.candidates,
                generated_candidates=context.generated_candidates,
                traces=[],
                limitations=[],
            )
        )
    return GraphBuilder().build(
        graph_id=graph_id,
        ranking_runs=ranking_runs,
        artifact_dir=artifact_dir if artifact_dir.exists() else None,
    )


def _maybe_apply_codex_drafting(
    hypotheses: list[ResearchHypothesis],
    *,
    graph: KnowledgeGraph,
    context: PipelineContext,
    output_dir: Path,
) -> tuple[list[ResearchHypothesis], dict[str, Any]]:
    if not bool(context.config.get("use_codex_hypothesis_drafting", False)):
        return hypotheses, {"enabled": False, "accepted_count": 0, "fallback_count": 0}
    provider = context.config.get("codex_hypothesis_drafting_provider")
    if provider is None:
        return hypotheses, {
            "enabled": True,
            "accepted_count": 0,
            "fallback_count": len(hypotheses),
            "warning": "No Codex drafting provider configured; deterministic wording used.",
        }
    drafter = CodexHypothesisDrafter(provider, working_directory=output_dir)
    allowed = allowed_hypothesis_reference_sets(graph)
    updated: list[ResearchHypothesis] = []
    accepted = 0
    fallback = 0
    for hypothesis in hypotheses:
        draft = drafter.draft_hypothesis_statement(
            hypothesis,
            allowed_entity_ids=allowed["entity_ids"],
            allowed_relation_ids=allowed["relation_ids"],
            allowed_provenance_ids=allowed["provenance_ids"],
            allowed_artifact_ids=allowed["artifact_ids"],
        )
        if draft.used_fallback or not draft.statement:
            fallback += 1
            updated.append(
                hypothesis.model_copy(
                    update={
                        "metadata": {
                            **hypothesis.metadata,
                            "codex_draft": draft.model_dump(mode="json"),
                        }
                    }
                )
            )
            continue
        accepted += 1
        updated.append(
            hypothesis.model_copy(
                update={
                    "statement": draft.statement,
                    "metadata": {
                        **hypothesis.metadata,
                        "codex_draft": draft.model_dump(mode="json"),
                    },
                }
            )
        )
    return updated, {
        "enabled": True,
        "accepted_count": accepted,
        "fallback_count": fallback,
    }


def _validate_hypotheses(
    hypotheses: list[ResearchHypothesis],
    graph: KnowledgeGraph,
    *,
    strict: bool,
) -> None:
    allowed = allowed_hypothesis_reference_sets(graph)
    errors: list[str] = []
    for hypothesis in hypotheses:
        refs = {
            "entity_ids": {
                *hypothesis.disease_entity_ids,
                *hypothesis.target_entity_ids,
                *hypothesis.molecule_entity_ids,
                *hypothesis.generated_molecule_entity_ids,
                *hypothesis.scaffold_entity_ids,
                *hypothesis.mechanism_entity_ids,
            },
            "relation_ids": {
                *hypothesis.supporting_relation_ids,
                *hypothesis.contradicting_relation_ids,
            },
            "artifact_ids": {*hypothesis.source_artifact_ids},
        }
        for bucket, values in refs.items():
            unknown = sorted(values - allowed[bucket])
            errors.extend(
                f"{hypothesis.hypothesis_id} references unknown {bucket[:-1]}: {value}"
                for value in unknown
            )
        guardrail_text = " ".join(
            [
                hypothesis.title,
                hypothesis.statement,
            ]
        )
        errors.extend(
            f"{hypothesis.hypothesis_id}: {warning}"
            for warning in detect_hypothesis_guardrail_violations(guardrail_text)
        )
    if errors and strict:
        raise ValueError("; ".join(errors))


def _persist_hypothesis_records(
    store_path: Path,
    *,
    hypotheses: list[ResearchHypothesis],
    evidence_gaps_by_id: dict[str, list[EvidenceGap]],
    criteria_by_id: dict[str, list[FalsificationCriterion]],
    questions_by_id: dict[str, list[TestableResearchQuestion]],
    run: HypothesisGenerationRun,
) -> None:
    store = HypothesisStore(store_path)
    for hypothesis in hypotheses:
        try:
            store.create_hypothesis(hypothesis)
        except ValueError:
            store.update_hypothesis(
                hypothesis.hypothesis_id,
                hypothesis.model_dump(),
                actor="HypothesisGenerationAgent",
            )
        for gap in evidence_gaps_by_id.get(hypothesis.hypothesis_id, []):
            store.add_evidence_gap(gap)
        for criterion in criteria_by_id.get(hypothesis.hypothesis_id, []):
            store.add_falsification_criterion(criterion)
        for question in questions_by_id.get(hypothesis.hypothesis_id, []):
            store.add_research_question(question)
    store.add_generation_run(run)


def _write_artifacts(
    *,
    output_dir: Path,
    store_path: Path,
    graph: KnowledgeGraph,
    hypotheses: list[ResearchHypothesis],
    evidence_gaps_by_id: dict[str, list[EvidenceGap]],
    criteria_by_id: dict[str, list[FalsificationCriterion]],
    questions_by_id: dict[str, list[TestableResearchQuestion]],
    run: HypothesisGenerationRun,
) -> dict[str, Path]:
    hypotheses_path = output_dir / "hypotheses.json"
    questions_path = output_dir / "research_questions.json"
    criteria_path = output_dir / "falsification_criteria.json"
    gaps_path = output_dir / "evidence_gaps.json"
    lifecycle_path = output_dir / "hypothesis_lifecycle.json"
    report_path = output_dir / "hypothesis_report.md"
    lifecycle_events = HypothesisStore(store_path).list_lifecycle_events()
    hypotheses_payload = {
        "schema_version": "1.6",
        "graph_id": graph.graph_id,
        "generation_run": run.model_dump(mode="json"),
        "hypotheses": [hypothesis.model_dump(mode="json") for hypothesis in hypotheses],
        "boundaries": [
            "A hypothesis is not evidence.",
            "A research question is not a lab protocol.",
            "A validation plan is not an experimental procedure.",
        ],
    }
    questions_payload = {
        "schema_version": "1.6",
        "graph_id": graph.graph_id,
        "questions": {
            hypothesis_id: [question.model_dump(mode="json") for question in questions]
            for hypothesis_id, questions in questions_by_id.items()
        },
    }
    criteria_payload = {
        "schema_version": "1.6",
        "graph_id": graph.graph_id,
        "falsification_criteria": {
            hypothesis_id: [criterion.model_dump(mode="json") for criterion in criteria]
            for hypothesis_id, criteria in criteria_by_id.items()
        },
    }
    gaps_payload = {
        "schema_version": "1.6",
        "graph_id": graph.graph_id,
        "evidence_gaps": {
            hypothesis_id: [gap.model_dump(mode="json") for gap in gaps]
            for hypothesis_id, gaps in evidence_gaps_by_id.items()
        },
    }
    lifecycle_payload = {
        "schema_version": "1.6",
        "graph_id": graph.graph_id,
        "lifecycle_events": [event.model_dump(mode="json") for event in lifecycle_events],
    }
    hypotheses_path.write_text(
        json.dumps(hypotheses_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    questions_path.write_text(
        json.dumps(questions_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    criteria_path.write_text(
        json.dumps(criteria_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gaps_path.write_text(
        json.dumps(gaps_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lifecycle_path.write_text(
        json.dumps(lifecycle_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        render_hypothesis_report_markdown(
            hypotheses,
            evidence_gaps_by_hypothesis=evidence_gaps_by_id,
            criteria_by_hypothesis=criteria_by_id,
            questions_by_hypothesis=questions_by_id,
            lifecycle_events=lifecycle_events,
        ),
        encoding="utf-8",
    )
    return {
        "hypotheses": hypotheses_path,
        "research_questions": questions_path,
        "falsification_criteria": criteria_path,
        "evidence_gaps": gaps_path,
        "hypothesis_lifecycle": lifecycle_path,
        "hypothesis_report": report_path,
    }


def _hypothesis_output_dir(context: PipelineContext) -> Path:
    base = _artifact_dir(context)
    configured = context.config.get("hypothesis_output_dir")
    return Path(str(configured)) if configured else base / "hypotheses"


def _artifact_dir(context: PipelineContext) -> Path:
    if context.output_dir is not None:
        return context.output_dir
    return Path(str(context.config.get("results_dir", "results")))


def _hypothesis_store_path(context: PipelineContext, output_dir: Path) -> Path:
    configured = context.config.get("hypothesis_store_path")
    return Path(str(configured)) if configured else output_dir / "hypotheses.sqlite"


def _positive_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _strict_guardrails(context: PipelineContext) -> bool:
    return bool(context.config.get("strict_hypothesis_guardrails", True))


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _portfolio_selections(context: PipelineContext) -> list[Any]:
    payload = context.config.get("portfolio_optimization")
    if isinstance(payload, dict):
        selection = payload.get("optimization_run", {}).get("selections")
        if isinstance(selection, list):
            return selection
    return []


def _graph_build_id(graph: KnowledgeGraph) -> str | None:
    if graph.build_runs:
        return graph.build_runs[-1].graph_build_id
    return graph.graph_id


def _graph_artifact_ids(graph: KnowledgeGraph) -> list[str]:
    artifact_ids = {f"graph:{graph.graph_id}", graph.graph_id}
    for entity in graph.entities:
        artifact_ids.update(entity.source_artifact_ids)
    for relation in graph.relations:
        artifact_ids.update(relation.source_artifact_ids)
    return sorted(artifact_ids)
