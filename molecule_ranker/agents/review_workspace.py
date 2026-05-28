from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.contracts import with_artifact_contract_metadata
from molecule_ranker.review.dashboard import generate_static_review_dashboard
from molecule_ranker.review.queue_builder import build_review_workspace
from molecule_ranker.review.schemas import Reviewer
from molecule_ranker.review.workspace import ReviewWorkspaceStore
from molecule_ranker.schemas import RankingRun
from molecule_ranker.utils import slugify


class ReviewWorkspaceAgent(BaseAgent):
    """Create and persist a local expert-review workspace when enabled."""

    name = "ReviewWorkspaceAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_review_workflow", False)):
            context.config["review_workflow_enabled"] = False
            return context
        if context.disease is None:
            raise ValueError("Review workspace requires a resolved disease.")

        ranking_run = RankingRun(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            generated_candidates=(
                context.generated_candidates
                if bool(context.config.get("include_generated_in_review", True))
                else []
            ),
            traces=context.traces,
            limitations=list(context.config.get("limitations", [])),
        )
        reviewer = _reviewer_from_config(context.config)
        workspace = build_review_workspace(
            ranking_run,
            config=_review_builder_config(context.config),
            report_artifacts=_report_artifacts(context),
            reviewer=reviewer,
        )
        max_items = int(context.config.get("max_review_items", 100) or 100)
        if len(workspace.review_items) > max_items:
            workspace.review_items = workspace.review_items[:max_items]

        db_path = Path(
            context.config.get("review_db_path") or ".review/molecule-ranker-review.sqlite"
        )
        ReviewWorkspaceStore(db_path).create_workspace(workspace)

        output_dir = _review_output_dir(context)
        output_dir.mkdir(parents=True, exist_ok=True)
        queue_path = output_dir / "review_queue.json"
        queue_payload = {
            **workspace.model_dump(mode="json"),
            "summary": _queue_summary(workspace.review_items),
        }
        queue_payload = with_artifact_contract_metadata(queue_payload, "review_queue")
        queue_path.write_text(json.dumps(queue_payload, indent=2, sort_keys=True) + "\n")

        summary = _queue_summary(workspace.review_items)
        context.output_dir = output_dir
        context.config["review_workflow_enabled"] = True
        context.config["review_workspace_id"] = workspace.workspace_id
        context.config["review_db_path"] = str(db_path)
        context.config["review_queue_json"] = str(queue_path)
        context.config["review_queue_summary"] = summary
        if bool(context.config.get("generate_review_dashboard", False)):
            dashboard_dir = Path(
                context.config.get("review_dashboard_dir") or output_dir / "review_dashboard"
            )
            dashboard_path = generate_static_review_dashboard(workspace, dashboard_dir)
            context.config["review_dashboard_path"] = str(dashboard_path)
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if not bool(context.config.get("review_workflow_enabled", False)):
            return "Review workflow disabled."
        return (
            "Created review workspace "
            f"{context.config.get('review_workspace_id')} with "
            f"{context.config.get('review_queue_summary', {}).get('review_item_count', 0)} items."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        enabled = bool(context.config.get("review_workflow_enabled", False))
        summary = context.config.get("review_queue_summary")
        summary = summary if isinstance(summary, dict) else {}
        metadata: dict[str, Any] = {
            "enabled": enabled,
            "workspace_id": context.config.get("review_workspace_id"),
            "review_db_path": context.config.get("review_db_path"),
            "review_item_count": int(summary.get("review_item_count", 0) or 0),
            "priority_distribution": summary.get("priority_distribution", {}),
            "generated_included": bool(context.config.get("include_generated_in_review", True)),
        }
        if context.config.get("review_dashboard_path"):
            metadata["review_dashboard_path"] = context.config.get("review_dashboard_path")
        reviewer = _reviewer_from_config(context.config)
        if reviewer is not None:
            metadata["reviewer"] = reviewer.model_dump(mode="json")
        return metadata


def _reviewer_from_config(config: dict[str, Any]) -> Reviewer | None:
    reviewer_id = config.get("reviewer_id")
    if not reviewer_id:
        return None
    return Reviewer(
        reviewer_id=str(reviewer_id),
        name=str(config["reviewer_name"]) if config.get("reviewer_name") else None,
        role=str(config["reviewer_role"]) if config.get("reviewer_role") else None,
    )


def _review_builder_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": config.get("run_id") or config.get("review_run_id"),
        "allow_generated_high_priority": bool(
            config.get("generated_high_priority_allowed", False)
        ),
        "review_priority_policy": config.get("review_priority_policy", "conservative"),
        "require_structure_for_review": bool(config.get("require_structure_for_review", False)),
    }


def _review_output_dir(context: PipelineContext) -> Path:
    if context.output_dir is not None:
        return context.output_dir
    results_dir = Path(context.config.get("results_dir") or "results")
    if context.disease is None:
        return results_dir
    return results_dir / slugify(context.disease.canonical_name)


def _report_artifacts(context: PipelineContext) -> dict[str, str]:
    output_dir = _review_output_dir(context)
    return {
        "report_md": str(output_dir / "report.md"),
        "candidates_json": str(output_dir / "candidates.json"),
        "generated_candidates_json": str(output_dir / "generated_candidates.json"),
        "developability_json": str(output_dir / "developability.json"),
        "trace_json": str(output_dir / "trace.json"),
    }


def _queue_summary(items: list[Any]) -> dict[str, Any]:
    priority_distribution: dict[str, int] = {}
    origin_distribution: dict[str, int] = {}
    status_distribution: dict[str, int] = {}
    for item in items:
        priority_distribution[item.priority_bucket] = (
            priority_distribution.get(item.priority_bucket, 0) + 1
        )
        origin_distribution[item.candidate_origin] = (
            origin_distribution.get(item.candidate_origin, 0) + 1
        )
        status_distribution[item.review_status] = (
            status_distribution.get(item.review_status, 0) + 1
        )
    return {
        "review_item_count": len(items),
        "priority_distribution": dict(sorted(priority_distribution.items())),
        "origin_distribution": dict(sorted(origin_distribution.items())),
        "status_distribution": dict(sorted(status_distribution.items())),
    }
