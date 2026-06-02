from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from molecule_ranker.platform.database import artifact_records
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.jobs import JobResult
from molecule_ranker.platform.schemas import PlatformJob
from molecule_ranker.workers.base import BaseWorker, JobHandler

PORTFOLIO_JOB_TYPES = {
    "portfolio_build_candidates",
    "portfolio_optimize",
    "portfolio_scenario_analysis",
    "portfolio_stage_gate",
    "portfolio_batch_build",
    "portfolio_memo",
}
GRAPH_JOB_TYPES = {
    "graph_build",
    "graph_query",
    "graph_mechanism_extract",
    "graph_contradiction_scan",
    "graph_staleness_scan",
    "graph_recommendation",
    "graph_export",
}
HYPOTHESIS_JOB_TYPES = {
    "hypothesis_generate",
    "hypothesis_rank",
    "hypothesis_questions",
    "hypothesis_report",
    "hypothesis_review",
}


def default_pipeline_handlers(
    root_dir: Path,
    database: PlatformDatabase | None = None,
) -> dict[str, JobHandler]:
    def placeholder(job: PlatformJob) -> JobResult:
        return _write_job_payload(
            root_dir,
            job,
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": "succeeded",
            },
        )

    def portfolio_handler(job: PlatformJob) -> JobResult:
        payload = _portfolio_job_payload(job)
        return _write_job_payload(root_dir, job, payload)

    def graph_handler(job: PlatformJob) -> JobResult:
        if database is None:
            raise ValueError("Graph jobs require a platform database.")
        return _run_graph_job(root_dir, database, job)

    def hypothesis_handler(job: PlatformJob) -> JobResult:
        if database is None:
            raise ValueError("Hypothesis jobs require a platform database.")
        return _run_hypothesis_job(root_dir, database, job)

    return {
        "ranking": placeholder,
        "generation": placeholder,
        "developability": placeholder,
        "experiment_import": placeholder,
        "active_learning": placeholder,
        "review_export": placeholder,
        "dashboard_build": placeholder,
        "portfolio_build_candidates": portfolio_handler,
        "portfolio_optimize": portfolio_handler,
        "portfolio_scenario_analysis": portfolio_handler,
        "portfolio_stage_gate": portfolio_handler,
        "portfolio_batch_build": portfolio_handler,
        "portfolio_memo": portfolio_handler,
        "graph_build": graph_handler,
        "graph_query": graph_handler,
        "graph_mechanism_extract": graph_handler,
        "graph_contradiction_scan": graph_handler,
        "graph_staleness_scan": graph_handler,
        "graph_recommendation": graph_handler,
        "graph_export": graph_handler,
        "hypothesis_generate": hypothesis_handler,
        "hypothesis_rank": hypothesis_handler,
        "hypothesis_questions": hypothesis_handler,
        "hypothesis_report": hypothesis_handler,
        "hypothesis_review": hypothesis_handler,
    }


def _write_job_payload(root_dir: Path, job: PlatformJob, payload: dict[str, object]) -> JobResult:
    output_dir = root_dir / ".molecule-ranker" / "job_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job.job_id}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return JobResult(
        result={"message": f"{job.job_type} completed by placeholder worker."},
        artifact_paths=[output_path],
    )


def _portfolio_job_payload(job: PlatformJob) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": "succeeded",
        "portfolio_boundary": "advisory_until_approved",
        "approved": False,
        "codex_memo_not_final_decision": True,
        "config_snapshot": job.config_snapshot,
    }
    if job.job_type == "portfolio_optimize" and isinstance(
        job.config_snapshot.get("candidates"), list
    ):
        payload["optimization_run"] = _run_configured_portfolio_optimization(job)
        payload["deterministic_validation"] = True
    return payload


def _run_configured_portfolio_optimization(job: PlatformJob) -> dict[str, object]:
    from molecule_ranker.portfolio.constraints import default_constraints
    from molecule_ranker.portfolio.objectives import default_objectives
    from molecule_ranker.portfolio.optimizer import PortfolioOptimizer
    from molecule_ranker.portfolio.schemas import (
        Portfolio,
        PortfolioCandidate,
        Program,
        ResourceBudget,
    )

    config = job.config_snapshot
    candidate_payloads = config.get("candidates")
    if not isinstance(candidate_payloads, list):
        raise ValueError(
            "portfolio_optimize requires a candidates list for deterministic execution."
        )
    candidates = [
        PortfolioCandidate.model_validate(_normalize_portfolio_candidate_payload(index, item))
        for index, item in enumerate(candidate_payloads, start=1)
        if isinstance(item, dict)
    ]
    program = Program(
        program_id=str(config.get("program_id") or job.project_id or "hosted-program"),
        name=str(config.get("program_name") or "Hosted portfolio program"),
        disease_focus=_string_list(config.get("disease_focus")),
        target_focus=_string_list(config.get("target_focus")),
    )
    portfolio = Portfolio(
        portfolio_id=str(config.get("portfolio_id") or f"portfolio-{job.job_id}"),
        program=program,
        candidates=candidates,
        objectives=default_objectives(),
        constraints=default_constraints(),
        budget=ResourceBudget(
            max_candidates=_optional_int(config.get("max_candidates")),
            max_generated_candidates=_optional_int(config.get("max_generated_candidates")),
            max_total_cost=_optional_float(config.get("max_total_cost")),
            max_assay_slots=_optional_int(config.get("max_assay_slots")),
            max_review_hours=_optional_float(config.get("max_review_hours")),
        ),
        metadata={"project_id": job.project_id, "job_id": job.job_id},
    )
    run = PortfolioOptimizer(
        algorithm=str(config.get("algorithm") or "greedy"),
        random_seed=int(config.get("random_seed") or 0),
    ).optimize(portfolio)
    return run.model_dump(mode="json")


def _run_graph_job(root_dir: Path, database: PlatformDatabase, job: PlatformJob) -> JobResult:
    output_dir = root_dir / ".molecule-ranker" / "job_outputs" / job.job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    if job.job_type == "graph_build":
        graph = _build_graph_for_job(root_dir, job)
        output_path = output_dir / "knowledge_graph.json"
        _write_json(output_path, graph)
        return JobResult(
            result={
                "artifact_type": "knowledge_graph",
                "graph_id": graph.graph_id,
                "entity_count": len(graph.entities),
                "relation_count": len(graph.relations),
                "mechanism_count": len(graph.mechanisms),
                "graph_boundary": "memory_and_reasoning_layer_not_new_biomedical_truth",
            },
            artifact_paths=[output_path],
        )
    graph = _load_graph_for_job(root_dir, database, job)
    if job.job_type == "graph_query":
        results = _run_graph_query(
            graph,
            query=str(job.config_snapshot.get("query") or ""),
            target_symbol=_optional_text(job.config_snapshot.get("target_symbol")),
            disease=_optional_text(job.config_snapshot.get("disease")),
            candidate_id=_optional_text(job.config_snapshot.get("candidate_id")),
            molecule_id=_optional_text(job.config_snapshot.get("molecule_id")),
        )
        output_path = output_dir / "graph_query_results.json"
        _write_json(output_path, {"query_results": results})
        return JobResult(
            result={
                "artifact_type": "knowledge_graph_query",
                "query_result_count": len(results),
                "graph_derived_summary_only": True,
            },
            artifact_paths=[output_path],
        )
    if job.job_type == "graph_mechanism_extract":
        from molecule_ranker.knowledge_graph.mechanism import extract_mechanism_hypotheses

        mechanisms = extract_mechanism_hypotheses(graph)
        output_path = output_dir / "mechanism_hypotheses.json"
        _write_json(output_path, {"mechanisms": mechanisms})
        return JobResult(
            result={
                "artifact_type": "knowledge_graph_mechanisms",
                "mechanism_count": len(mechanisms),
                "not_proof_of_causality": True,
            },
            artifact_paths=[output_path],
        )
    if job.job_type == "graph_contradiction_scan":
        from molecule_ranker.knowledge_graph.contradiction import build_contradiction_report

        report = build_contradiction_report(graph)
        output_path = output_dir / "contradiction_report.json"
        _write_json(output_path, report)
        return JobResult(
            result={
                "artifact_type": "knowledge_graph_contradictions",
                "contradiction_count": len(report.contradiction_relations),
                "advisory": True,
            },
            artifact_paths=[output_path],
        )
    if job.job_type == "graph_staleness_scan":
        from molecule_ranker.knowledge_graph.contradiction import build_staleness_report

        report = build_staleness_report(graph)
        output_path = output_dir / "staleness_report.json"
        _write_json(output_path, report)
        return JobResult(
            result={
                "artifact_type": "knowledge_graph_staleness",
                "stale_relation_count": len(report.stale_relations),
                "advisory": True,
            },
            artifact_paths=[output_path],
        )
    if job.job_type == "graph_recommendation":
        from molecule_ranker.knowledge_graph.recommendations import generate_graph_recommendations

        recommendations = generate_graph_recommendations(
            graph,
            current_project_id=job.project_id,
            current_program_id=_optional_text(job.config_snapshot.get("program_id")),
        )
        output_path = output_dir / "graph_recommendations.json"
        _write_json(
            output_path,
            {
                "recommendations": recommendations,
                "advisory": True,
                "automatic_decisions_disabled": True,
            },
        )
        return JobResult(
            result={
                "artifact_type": "knowledge_graph_recommendations",
                "recommendation_count": len(recommendations),
                "advisory": True,
                "automatic_decisions_disabled": True,
            },
            artifact_paths=[output_path],
        )
    if job.job_type == "graph_export":
        return _export_graph_for_job(output_dir, job, graph)
    raise ValueError(f"Unsupported graph job type: {job.job_type}")


def _build_graph_for_job(root_dir: Path, job: PlatformJob) -> Any:
    from molecule_ranker.knowledge_graph.builder import GraphBuilder
    from molecule_ranker.knowledge_graph.mechanism import extract_mechanism_hypotheses
    from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph

    config = job.config_snapshot
    graph_id = str(config.get("graph_id") or f"kg-{job.project_id or job.job_id}")
    artifact_paths = _configured_artifact_paths(config)
    artifact_payloads = config.get("artifact_payloads")
    if artifact_paths or isinstance(artifact_payloads, dict):
        return GraphBuilder().build_from_artifacts(
            graph_id=graph_id,
            artifact_paths=artifact_paths,
            artifact_payloads=artifact_payloads if isinstance(artifact_payloads, dict) else None,
        )
    directories = _graph_artifact_directories(_configured_artifact_directories(root_dir, job))
    if not directories:
        return KnowledgeGraph(
            graph_id=graph_id,
            metadata={
                "project_id": job.project_id,
                "job_id": job.job_id,
                "warning": "No graph source artifacts found for hosted build.",
            },
        )
    graphs = [
        GraphBuilder().build_from_directory(directory, graph_id=f"{graph_id}-{index + 1}")
        for index, directory in enumerate(directories)
    ]
    if len(graphs) == 1:
        graph = graphs[0].model_copy(update={"graph_id": graph_id})
        graph.mechanisms = extract_mechanism_hypotheses(graph)
        return graph
    entities = {entity.entity_id: entity for graph in graphs for entity in graph.entities}
    relations = {relation.relation_id: relation for graph in graphs for relation in graph.relations}
    graph = KnowledgeGraph(
        graph_id=graph_id,
        entities=sorted(entities.values(), key=lambda entity: entity.entity_id),
        relations=sorted(relations.values(), key=lambda relation: relation.relation_id),
        metadata={"project_id": job.project_id, "job_id": job.job_id},
    )
    graph.mechanisms = extract_mechanism_hypotheses(graph)
    return graph


def _load_graph_for_job(root_dir: Path, database: PlatformDatabase, job: PlatformJob) -> Any:
    from molecule_ranker.knowledge_graph.schemas import KnowledgeGraph

    config = job.config_snapshot
    if isinstance(config.get("graph"), dict):
        return KnowledgeGraph.model_validate(config["graph"])
    graph_path = config.get("graph_path")
    if graph_path is not None:
        return KnowledgeGraph.model_validate(
            json.loads(Path(str(graph_path)).read_text(encoding="utf-8"))
        )
    graph_artifact_id = config.get("graph_artifact_id")
    if graph_artifact_id is not None:
        path = _artifact_path_for_id(database, job, str(graph_artifact_id))
        return KnowledgeGraph.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return _build_graph_for_job(root_dir, job)


def _artifact_path_for_id(
    database: PlatformDatabase,
    job: PlatformJob,
    artifact_id: str,
) -> Path:
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(artifact_records).where(artifact_records.c.artifact_id == artifact_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        raise ValueError(f"Graph artifact not found: {artifact_id}")
    if job.project_id is not None and row["project_id"] not in {None, job.project_id}:
        raise PermissionError("Graph artifact belongs to a different project.")
    return Path(str(row["path"]))


def _run_graph_query(
    graph: Any,
    *,
    query: str,
    target_symbol: str | None,
    disease: str | None,
    candidate_id: str | None,
    molecule_id: str | None,
) -> list[Any]:
    from molecule_ranker.knowledge_graph.reasoning import GraphReasoner

    reasoner = GraphReasoner(graph)
    if query == "candidates_for_target":
        if not target_symbol:
            raise ValueError("target_symbol is required for candidates_for_target.")
        return reasoner.candidates_for_target(target_symbol)
    if query == "mechanisms_for_disease":
        if not disease:
            raise ValueError("disease is required for mechanisms_for_disease.")
        return reasoner.mechanisms_for_disease(disease)
    if query == "generated_molecules_without_direct_evidence":
        return reasoner.generated_molecules_without_direct_evidence()
    if query == "candidates_with_contradictory_evidence":
        return reasoner.candidates_with_contradictory_evidence()
    if query == "scaffolds_with_positive_assay_history":
        return reasoner.scaffolds_with_positive_assay_history()
    if query == "targets_with_repeated_developability_failures":
        return reasoner.targets_with_repeated_developability_failures()
    if query == "mechanisms_supported_across_programs":
        return reasoner.mechanisms_supported_across_programs()
    if query == "molecules_with_safety_concerns_across_programs":
        return reasoner.molecules_with_safety_concerns_across_programs()
    if query == "portfolios_reusing_same_scaffold_risk":
        return reasoner.portfolios_reusing_same_scaffold_risk()
    if query == "projects_with_stale_model_predictions":
        return reasoner.projects_with_stale_model_predictions()
    if query == "graph_paths_between_disease_and_molecule":
        if not disease or not molecule_id:
            raise ValueError(
                "disease and molecule_id are required for graph_paths_between_disease_and_molecule."
            )
        return reasoner.graph_paths_between_disease_and_molecule(disease, molecule_id)
    if query == "evidence_gaps_for_candidate":
        if not candidate_id:
            raise ValueError("candidate_id is required for evidence_gaps_for_candidate.")
        return reasoner.evidence_gaps_for_candidate(candidate_id)
    raise ValueError(f"Unsupported graph query: {query}")


def _export_graph_for_job(output_dir: Path, job: PlatformJob, graph: Any) -> JobResult:
    from molecule_ranker.knowledge_graph.export import (
        export_graph_csv,
        export_graph_json,
        export_graph_turtle,
    )

    export_format = str(job.config_snapshot.get("output_format") or "json").lower()
    if export_format == "json":
        output_path = output_dir / "graph_export.json"
        export_graph_json(graph, output_path)
        artifact_paths = [output_path]
    elif export_format == "csv":
        paths = export_graph_csv(graph, output_dir / "csv")
        artifact_paths = list(paths.values())
    elif export_format == "ttl":
        output_path = output_dir / "graph_export.ttl"
        export_graph_turtle(graph, output_path)
        artifact_paths = [output_path]
    else:
        raise ValueError("Unsupported graph export format.")
    return JobResult(
        result={
            "artifact_type": "knowledge_graph_export",
            "format": export_format,
            "artifact_count": len(artifact_paths),
            "secrets_removed": True,
            "graph_boundary": "exported_graph_is_not_new_biomedical_truth",
        },
        artifact_paths=artifact_paths,
    )


def _run_hypothesis_job(root_dir: Path, database: PlatformDatabase, job: PlatformJob) -> JobResult:
    from molecule_ranker.hypotheses.evidence_gap import analyze_evidence_gaps_for_hypotheses
    from molecule_ranker.hypotheses.falsification import build_falsification_criteria_for_hypotheses
    from molecule_ranker.hypotheses.generator import DeterministicHypothesisGenerator
    from molecule_ranker.hypotheses.questions import plan_research_questions_for_hypotheses
    from molecule_ranker.hypotheses.ranking import rank_research_hypotheses
    from molecule_ranker.hypotheses.reports import render_hypothesis_report_markdown
    from molecule_ranker.hypotheses.review import HypothesisReviewService
    from molecule_ranker.hypotheses.schemas import HypothesisGenerationRun, ResearchHypothesis
    from molecule_ranker.hypotheses.store import HypothesisStore
    from molecule_ranker.knowledge_graph.store import KnowledgeGraphStore

    output_dir = root_dir / ".molecule-ranker" / "job_outputs" / job.job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    store = HypothesisStore(_hosted_hypothesis_store_path(root_dir, job.project_id))

    if job.job_type == "hypothesis_generate":
        graph = _load_graph_for_job(root_dir, database, job)
        graph_store = KnowledgeGraphStore(output_dir / "graph_store")
        graph_store.save(graph, actor=job.requested_by_user_id, reason="hosted hypothesis job")
        generated = DeterministicHypothesisGenerator(graph_store).generate()
        max_hypotheses = int(job.config_snapshot.get("max_hypotheses") or 100)
        hypotheses = [
            _with_hosted_hypothesis_metadata(hypothesis, job)
            for hypothesis in generated[: max(1, max_hypotheses)]
        ]
        gaps = analyze_evidence_gaps_for_hypotheses(hypotheses, graph=graph)
        criteria = build_falsification_criteria_for_hypotheses(hypotheses)
        questions = plan_research_questions_for_hypotheses(
            hypotheses,
            evidence_gaps_by_hypothesis=gaps,
            criteria_by_hypothesis=criteria,
        )
        ranked = rank_research_hypotheses(hypotheses, evidence_gaps_by_hypothesis=gaps)
        run = HypothesisGenerationRun(
            generation_run_id=f"hypothesis-run-{job.job_id}",
            project_id=job.project_id,
            program_id=_optional_text(job.config_snapshot.get("program_id")),
            graph_build_id=_optional_text(job.config_snapshot.get("graph_build_id")),
            input_artifact_ids=_string_list(job.config_snapshot.get("input_artifact_ids")),
            hypothesis_count=len(ranked),
            accepted_count=0,
            rejected_count=0,
            warnings=_hosted_hypothesis_warnings(ranked),
            metadata={
                "job_id": job.job_id,
                "deterministic_validation": True,
                "codex_drafting_used": bool(
                    job.config_snapshot.get("use_codex_hypothesis_drafting")
                ),
            },
        )
        _persist_hosted_hypotheses(store, ranked, gaps, criteria, questions, run)
        artifact_paths = _write_hypothesis_artifacts(
            output_dir,
            ranked,
            gaps,
            criteria,
            questions,
            store.list_lifecycle_events(),
        )
        return JobResult(
            result={
                "artifact_type": "hypothesis_generation",
                "hypothesis_count": len(ranked),
                "deterministic_validation": True,
                "hypotheses_are_not_evidence": True,
                "generated_hypothesis_ids_requiring_review": [
                    hypothesis.hypothesis_id
                    for hypothesis in ranked
                    if hypothesis.hypothesis_type == "generated_molecule"
                ],
            },
            artifact_paths=artifact_paths,
        )

    hypotheses = _hypotheses_for_job(store, job, ResearchHypothesis)
    if job.job_type == "hypothesis_rank":
        gaps = {
            hypothesis.hypothesis_id: store.list_evidence_gaps(hypothesis.hypothesis_id)
            for hypothesis in hypotheses
        }
        ranked = rank_research_hypotheses(hypotheses, evidence_gaps_by_hypothesis=gaps)
        for hypothesis in ranked:
            store.update_hypothesis(
                hypothesis.hypothesis_id,
                hypothesis.model_dump(),
                actor="PipelineWorker",
            )
        output_path = output_dir / "ranked_hypotheses.json"
        _write_json(output_path, {"hypotheses": ranked, "ranking_is_for_planning": True})
        return JobResult(
            result={"artifact_type": "ranked_hypotheses", "hypothesis_count": len(ranked)},
            artifact_paths=[output_path],
        )
    if job.job_type == "hypothesis_questions":
        gaps = {
            hypothesis.hypothesis_id: store.list_evidence_gaps(hypothesis.hypothesis_id)
            for hypothesis in hypotheses
        }
        criteria = {
            hypothesis.hypothesis_id: store.list_falsification_criteria(
                hypothesis.hypothesis_id
            )
            for hypothesis in hypotheses
        }
        questions = plan_research_questions_for_hypotheses(
            hypotheses,
            evidence_gaps_by_hypothesis=gaps,
            criteria_by_hypothesis=criteria,
        )
        for items in questions.values():
            for question in items:
                store.add_research_question(question)
        output_path = output_dir / "research_questions.json"
        _write_json(
            output_path,
            {"questions": questions, "questions_are_not_protocols": True},
        )
        return JobResult(
            result={
                "artifact_type": "research_questions",
                "question_count": sum(len(v) for v in questions.values()),
            },
            artifact_paths=[output_path],
        )
    if job.job_type == "hypothesis_report":
        gaps = {
            hypothesis.hypothesis_id: store.list_evidence_gaps(hypothesis.hypothesis_id)
            for hypothesis in hypotheses
        }
        criteria = {
            hypothesis.hypothesis_id: store.list_falsification_criteria(
                hypothesis.hypothesis_id
            )
            for hypothesis in hypotheses
        }
        questions = {
            hypothesis.hypothesis_id: store.list_research_questions(hypothesis.hypothesis_id)
            for hypothesis in hypotheses
        }
        output_path = output_dir / "hypothesis_report.md"
        output_path.write_text(
            render_hypothesis_report_markdown(
                hypotheses,
                evidence_gaps_by_hypothesis=gaps,
                criteria_by_hypothesis=criteria,
                questions_by_hypothesis=questions,
                lifecycle_events=store.list_lifecycle_events(),
            ),
            encoding="utf-8",
        )
        return JobResult(
            result={"artifact_type": "hypothesis_report", "hypotheses_are_not_evidence": True},
            artifact_paths=[output_path],
        )
    if job.job_type == "hypothesis_review":
        decision = HypothesisReviewService(store).record_decision(
            str(job.config_snapshot.get("hypothesis_id") or ""),
            reviewer_id=str(job.config_snapshot.get("reviewer_id") or job.requested_by_user_id),
            decision=str(job.config_snapshot.get("decision") or "hold"),
            rationale=str(job.config_snapshot.get("rationale") or "Hosted hypothesis review."),
            human_approval=bool(job.config_snapshot.get("human_review_approved")),
            metadata={"job_id": job.job_id, "hosted_platform": True},
        )
        output_path = output_dir / "hypothesis_review.json"
        _write_json(
            output_path,
            {
                "review_decision": decision,
                "review_decision_is_not_evidence": True,
                "lifecycle_events": store.list_lifecycle_events(decision.hypothesis_id),
            },
        )
        database.write_audit(
            "hypothesis_review_status_changed",
            actor_user_id=job.requested_by_user_id,
            project_id=job.project_id,
            summary=f"Recorded hypothesis review for {decision.hypothesis_id}.",
            object_type="hypothesis",
            object_id=decision.hypothesis_id,
            metadata={
                "decision": decision.decision,
                "decision_id": decision.decision_id,
                "review_decision_is_not_evidence": True,
            },
        )
        return JobResult(
            result={
                "artifact_type": "hypothesis_review",
                "hypothesis_id": decision.hypothesis_id,
                "review_decision_is_not_evidence": True,
            },
            artifact_paths=[output_path],
        )
    raise ValueError(f"Unsupported hypothesis job type: {job.job_type}")


def _with_hosted_hypothesis_metadata(hypothesis: Any, job: PlatformJob) -> Any:
    metadata = {
        **hypothesis.metadata,
        "project_id": job.project_id,
        "program_id": job.config_snapshot.get("program_id"),
        "hosted_platform": True,
        "job_id": job.job_id,
        "deterministic_validation": True,
        "hypothesis_is_not_evidence": True,
    }
    if job.config_snapshot.get("use_codex_hypothesis_drafting"):
        metadata["codex_draft"] = {
            "status": "not_used_by_hosted_worker",
            "deterministic_validation_required": True,
        }
    return hypothesis.model_copy(update={"metadata": metadata})


def _persist_hosted_hypotheses(
    store: Any,
    hypotheses: list[Any],
    gaps: dict[str, list[Any]],
    criteria: dict[str, list[Any]],
    questions: dict[str, list[Any]],
    run: Any,
) -> None:
    for hypothesis in hypotheses:
        try:
            store.create_hypothesis(hypothesis)
        except ValueError:
            store.update_hypothesis(
                hypothesis.hypothesis_id,
                hypothesis.model_dump(),
                actor="PipelineWorker",
            )
        for gap in gaps.get(hypothesis.hypothesis_id, []):
            store.add_evidence_gap(gap)
        for criterion in criteria.get(hypothesis.hypothesis_id, []):
            store.add_falsification_criterion(criterion)
        for question in questions.get(hypothesis.hypothesis_id, []):
            store.add_research_question(question)
    store.add_generation_run(run)


def _write_hypothesis_artifacts(
    output_dir: Path,
    hypotheses: list[Any],
    gaps: dict[str, list[Any]],
    criteria: dict[str, list[Any]],
    questions: dict[str, list[Any]],
    lifecycle_events: list[Any],
) -> list[Path]:
    from molecule_ranker.hypotheses.reports import render_hypothesis_report_markdown

    paths = {
        "hypotheses": output_dir / "hypotheses.json",
        "research_questions": output_dir / "research_questions.json",
        "falsification_criteria": output_dir / "falsification_criteria.json",
        "evidence_gaps": output_dir / "evidence_gaps.json",
        "hypothesis_lifecycle": output_dir / "hypothesis_lifecycle.json",
        "hypothesis_report": output_dir / "hypothesis_report.md",
    }
    _write_json(
        paths["hypotheses"],
        {
            "hypotheses": hypotheses,
            "hypotheses_are_not_evidence": True,
            "generated_molecules_remain_computational_hypotheses": True,
        },
    )
    _write_json(paths["research_questions"], {"questions": questions})
    _write_json(paths["falsification_criteria"], {"falsification_criteria": criteria})
    _write_json(paths["evidence_gaps"], {"evidence_gaps": gaps})
    _write_json(paths["hypothesis_lifecycle"], {"lifecycle_events": lifecycle_events})
    paths["hypothesis_report"].write_text(
        render_hypothesis_report_markdown(
            hypotheses,
            evidence_gaps_by_hypothesis=gaps,
            criteria_by_hypothesis=criteria,
            questions_by_hypothesis=questions,
            lifecycle_events=lifecycle_events,
        ),
        encoding="utf-8",
    )
    return list(paths.values())


def _hypotheses_for_job(store: Any, job: PlatformJob, model: Any) -> list[Any]:
    configured = job.config_snapshot.get("hypotheses")
    if isinstance(configured, list):
        hypotheses = [model.model_validate(item) for item in configured if isinstance(item, dict)]
        for hypothesis in hypotheses:
            try:
                store.create_hypothesis(hypothesis)
            except ValueError:
                pass
        return hypotheses
    hypothesis_id = job.config_snapshot.get("hypothesis_id")
    if hypothesis_id:
        return [store.get_hypothesis(str(hypothesis_id))]
    return store.list_hypotheses(project_id=job.project_id)


def _hosted_hypothesis_store_path(root_dir: Path, project_id: str | None) -> Path:
    project = project_id or "global"
    return root_dir / ".molecule-ranker" / "hypotheses" / project / "hypotheses.sqlite"


def _hosted_hypothesis_warnings(hypotheses: list[Any]) -> list[str]:
    warnings = ["Hypotheses are not evidence."]
    if any(hypothesis.hypothesis_type == "generated_molecule" for hypothesis in hypotheses):
        warnings.append(
            "Generated-molecule hypotheses require human review before follow-up planning."
        )
    return warnings


def _configured_artifact_paths(config: dict[str, Any]) -> list[str | Path] | dict[str, str | Path]:
    raw = config.get("artifact_paths")
    if isinstance(raw, dict):
        return {str(key): Path(str(value)) for key, value in raw.items()}
    if isinstance(raw, list):
        return [Path(str(value)) for value in raw]
    return []


def _configured_artifact_directories(root_dir: Path, job: PlatformJob) -> list[Path]:
    config = job.config_snapshot
    directories: list[Path] = []
    for key in ("artifact_dir", "from_project"):
        if config.get(key) is not None:
            directories.append(Path(str(config[key])))
    run_value = config.get("from_run") or config.get("run_id")
    if run_value is not None:
        run_path = Path(str(run_value))
        directories.append(run_path if run_path.is_absolute() else root_dir / str(run_value))
    directories.append(root_dir)
    try:
        from molecule_ranker.workspace.store import ProjectWorkspaceStore

        workspace = ProjectWorkspaceStore(root_dir).load()
    except ValueError:
        workspace = None
    if workspace is not None:
        directories.extend(Path(run.run_dir) for run in workspace.runs)
    return directories


def _graph_artifact_directories(directories: list[Path]) -> list[Path]:
    from molecule_ranker.knowledge_graph.builder import GraphBuilder

    seen: set[Path] = set()
    result: list[Path] = []
    for directory in directories:
        resolved = directory.resolve()
        if resolved in seen or not resolved.exists() or not resolved.is_dir():
            continue
        if any((resolved / filename).exists() for filename in GraphBuilder.ARTIFACT_FILENAMES):
            result.append(resolved)
            seen.add(resolved)
    return result


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n")


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_portfolio_candidate_payload(
    index: int,
    item: dict[str, object],
) -> dict[str, object]:
    data = dict(item)
    data.setdefault(
        "portfolio_candidate_id",
        data.get("source_candidate_id") or data.get("candidate_id") or f"candidate-{index}",
    )
    data.setdefault("candidate_name", data.get("name") or data["portfolio_candidate_id"])
    data.setdefault("origin", "existing")
    data.setdefault(
        "target_symbols",
        _string_list(data.get("target_symbols") or data.get("targets")),
    )
    data.setdefault("diversity_features", {})
    data.setdefault("risk_flags", [])
    data.setdefault("blocking_risks", [])
    data.setdefault("metadata", {})
    return data


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    return [str(value)]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


class PipelineWorker(BaseWorker):
    def __init__(
        self,
        *,
        database: PlatformDatabase,
        root_dir: Path | None = None,
        handlers: dict[str, JobHandler] | None = None,
    ) -> None:
        active_root = root_dir or database.root_dir
        super().__init__(
            database=database,
            handlers=handlers or default_pipeline_handlers(active_root, database=database),
            job_types={
                "ranking",
                "generation",
                "developability",
                "experiment_import",
                "active_learning",
                "review_export",
                "external_export",
                "dashboard_build",
                *PORTFOLIO_JOB_TYPES,
                *GRAPH_JOB_TYPES,
                *HYPOTHESIS_JOB_TYPES,
            },
        )
