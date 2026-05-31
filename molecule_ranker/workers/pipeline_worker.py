from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def default_pipeline_handlers(root_dir: Path) -> dict[str, JobHandler]:
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
            handlers=handlers or default_pipeline_handlers(active_root),
            job_types={
                "ranking",
                "generation",
                "developability",
                "experiment_import",
                "active_learning",
                "review_export",
                "dashboard_build",
                *PORTFOLIO_JOB_TYPES,
            },
        )
