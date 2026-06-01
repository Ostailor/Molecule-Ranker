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
                "dashboard_build",
                *PORTFOLIO_JOB_TYPES,
                *GRAPH_JOB_TYPES,
            },
        )
