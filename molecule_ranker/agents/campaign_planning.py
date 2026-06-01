from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.campaigns import (
    CampaignBudget,
    CampaignStore,
    build_budget_approval_gate,
    build_campaign_approval_gate,
    build_campaign_draft,
    build_generated_molecule_review_gate,
    build_safety_review_gate,
    check_budget_constraints,
    compute_campaign_budget_summary,
    plan_campaign,
    schedule_campaign_work,
)
from molecule_ranker.campaigns.reports import (
    build_campaign_memo,
    render_campaign_memo_markdown,
    render_campaign_report_markdown,
)
from molecule_ranker.campaigns.schemas import CampaignPlan, CampaignWorkPackage
from molecule_ranker.utils import slugify


class CampaignPlanningAgent(BaseAgent):
    """Run V1.7 deterministic campaign planning before review and reporting."""

    name = "CampaignPlanningAgent"

    def __init__(self) -> None:
        super().__init__()
        self._last_metadata: dict[str, Any] = self._disabled_metadata()

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_campaign_planning", False)):
            self._last_metadata = self._disabled_metadata()
            return context

        output_dir = _campaign_output_dir(context)
        output_dir.mkdir(parents=True, exist_ok=True)
        hypotheses_path = _required_artifact_path(
            context,
            direct_keys=("campaign_hypotheses_json", "hypotheses_json", "hypotheses_path"),
            nested_key="hypotheses",
            section="hypothesis_generation",
        )
        build_result = build_campaign_draft(
            hypotheses_path=hypotheses_path,
            research_questions_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_research_questions_json",
                    "research_questions_json",
                    "research_questions_path",
                ),
                nested_key="research_questions",
                section="hypothesis_generation",
            ),
            falsification_criteria_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_falsification_criteria_json",
                    "falsification_criteria_json",
                    "falsification_criteria_path",
                ),
                nested_key="falsification_criteria",
                section="hypothesis_generation",
            ),
            evidence_gaps_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_evidence_gaps_json",
                    "evidence_gaps_json",
                    "evidence_gaps_path",
                ),
                nested_key="evidence_gaps",
                section="hypothesis_generation",
            ),
            portfolio_optimization_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_portfolio_optimization_json",
                    "portfolio_optimization_json",
                    "portfolio_optimization_path",
                ),
                nested_key="portfolio_optimization",
                section="portfolio_optimization",
            ),
            active_learning_batch_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_active_learning_batch_json",
                    "active_learning_batch_json",
                    "active_learning_batch_path",
                ),
                nested_key="active_learning_batch",
                section="active_learning",
            )
            or _existing_output_artifact(output_dir, "active_learning_batch.json"),
            review_queue_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_review_queue_json",
                    "review_queue_json",
                    "review_queue_path",
                ),
                nested_key="review_queue",
                section="review_workspace",
            ),
            experimental_evidence_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_experimental_evidence_json",
                    "experimental_evidence_json",
                    "experimental_evidence_path",
                ),
                nested_key="experimental_evidence",
                section="experimental_evidence",
            ),
            model_predictions_path=_optional_artifact_path(
                context,
                direct_keys=("campaign_model_predictions_json", "model_predictions_json"),
                nested_key="model_predictions",
                section="predictive_model",
            ),
            structure_aware_assessments_path=_optional_artifact_path(
                context,
                direct_keys=(
                    "campaign_structure_aware_assessments_json",
                    "structure_assessments_json",
                ),
                nested_key="structure_aware_assessments",
                section="structure_assessments",
            ),
            knowledge_graph_artifact_paths=_knowledge_graph_paths(context),
            project_metadata=_project_metadata(context),
            program_metadata=_program_metadata(context),
            name=_optional_str(context.config.get("campaign_name")),
        )
        budget = _campaign_budget(build_result.campaign.campaign_id, context.config)
        work_packages = _apply_resource_estimates(
            build_result.work_packages,
            context.config,
        )[: _max_work_packages(context.config)]
        plan = plan_campaign(
            campaign=build_result.campaign,
            objectives=build_result.objectives,
            work_packages=work_packages,
            budget=budget,
            portfolio_outputs=_load_optional_mapping(
                _optional_artifact_path(
                    context,
                    direct_keys=(
                        "campaign_portfolio_optimization_json",
                        "portfolio_optimization_json",
                    ),
                    nested_key="portfolio_optimization",
                    section="portfolio_optimization",
                )
            ),
            hypothesis_ranking=_hypothesis_ranking(hypotheses_path),
            active_learning_suggestions=_active_learning_suggestions(context, output_dir),
            review_status=_review_status(context),
            experimental_evidence=_load_optional_mapping(
                _optional_artifact_path(
                    context,
                    direct_keys=(
                        "campaign_experimental_evidence_json",
                        "experimental_evidence_json",
                    ),
                    nested_key="experimental_evidence",
                    section="experimental_evidence",
                )
            ),
            model_uncertainty=_model_uncertainty(hypotheses_path),
            graph_contradictions=_graph_contradictions(context),
            config={
                "human_approval_required": bool(
                    context.config.get("require_campaign_approval", True)
                ),
                "require_generated_molecule_review": bool(
                    context.config.get("require_generated_review_gate", True)
                ),
                "campaign_planning_strategy": str(
                    context.config.get("campaign_planning_strategy", "balanced")
                ),
                "require_generated_review_gate": bool(
                    context.config.get("require_generated_review_gate", True)
                ),
            },
        )
        plan = _finalize_plan(plan, context.config)
        memo = build_campaign_memo(plan)

        store_path = Path(
            str(context.config.get("campaign_store_path") or output_dir / "campaigns.sqlite")
        )
        store = CampaignStore(store_path)
        _create_or_load_campaign(store, build_result.campaign)
        store.save_campaign_plan(plan)
        store.save_campaign_memo(memo)
        for gate in plan.stage_gates:
            store.add_stage_gate_decision(gate)

        artifact_paths = _write_campaign_artifacts(
            output_dir=output_dir,
            campaign=build_result.campaign,
            plan=plan,
            memo_markdown=render_campaign_memo_markdown(memo, plan),
            report_markdown=render_campaign_report_markdown(plan, build_result.campaign),
        )
        context.output_dir = output_dir
        context.config["campaign_planning"] = {
            "enabled": True,
            "saved": True,
            "campaign_id": plan.campaign_id,
            "campaign_plan_id": plan.campaign_plan_id,
            "work_package_count": len(plan.work_packages),
            "stage_gate_count": len(plan.stage_gates),
            "budget_summary": plan.budget_summary,
            "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
            "store_path": str(store_path),
            "human_approval_required": plan.human_approval_required,
            "claim_boundaries": [
                "research_management_artifact",
                "no_procedural_experimental_instructions",
                "no_synthesis_routes",
                "deterministic_campaign_artifacts_only",
            ],
        }
        self._last_metadata = {
            "enabled": True,
            "campaign_id": plan.campaign_id,
            "campaign_plan_id": plan.campaign_plan_id,
            "objective_count": len(plan.objectives),
            "work_package_count": len(plan.work_packages),
            "stage_gate_count": len(plan.stage_gates),
            "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
            "store_path": str(store_path),
            "budget_within_limits": plan.budget_summary.get("within_limits"),
        }
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        del context
        if not self._last_metadata.get("enabled", False):
            return "Campaign planning disabled."
        return (
            f"Built campaign plan {self._last_metadata.get('campaign_plan_id')} "
            f"with {self._last_metadata.get('work_package_count', 0)} work package(s)."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        del context
        return dict(self._last_metadata)

    def _disabled_metadata(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "reason": "enable_campaign_planning is false",
            "work_package_count": 0,
        }


def _campaign_output_dir(context: PipelineContext) -> Path:
    configured = context.config.get("campaign_output_dir")
    if configured:
        return Path(str(configured))
    if context.output_dir is not None:
        return context.output_dir
    results_dir = Path(str(context.config.get("results_dir") or "results"))
    if context.disease is not None:
        return results_dir / slugify(context.disease.canonical_name)
    return results_dir


def _required_artifact_path(
    context: PipelineContext,
    *,
    direct_keys: Sequence[str],
    nested_key: str,
    section: str,
) -> Path:
    path = _optional_artifact_path(
        context,
        direct_keys=direct_keys,
        nested_key=nested_key,
        section=section,
    )
    if path is None:
        raise ValueError(
            f"Campaign planning requires {nested_key} artifact from {section}."
        )
    return path


def _optional_artifact_path(
    context: PipelineContext,
    *,
    direct_keys: Sequence[str],
    nested_key: str,
    section: str,
) -> Path | None:
    for key in direct_keys:
        value = context.config.get(key)
        if value:
            return Path(str(value))
    raw_section = context.config.get(section)
    if isinstance(raw_section, Mapping):
        artifact_paths = raw_section.get("artifact_paths")
        if isinstance(artifact_paths, Mapping) and artifact_paths.get(nested_key):
            return Path(str(artifact_paths[nested_key]))
        if raw_section.get("artifact_path") and nested_key == section:
            return Path(str(raw_section["artifact_path"]))
    return None


def _existing_output_artifact(output_dir: Path, name: str) -> Path | None:
    candidate = output_dir / name
    return candidate if candidate.exists() else None


def _campaign_budget(campaign_id: str, config: Mapping[str, Any]) -> CampaignBudget:
    return CampaignBudget(
        budget_id=f"campaign-budget-{slugify(campaign_id)}",
        campaign_id=campaign_id,
        max_total_cost=_optional_float(config.get("campaign_budget_cost")),
        cost_units="relative" if config.get("campaign_budget_cost") is not None else None,
        max_assay_slots=_optional_int(config.get("campaign_budget_assay_slots")),
        max_review_hours=_optional_float(config.get("campaign_budget_review_hours")),
        max_compute_units=_optional_float(config.get("campaign_budget_compute_units")),
        max_codex_tasks=_optional_int(config.get("campaign_budget_codex_tasks")),
        max_external_sync_jobs=_optional_int(config.get("campaign_budget_external_sync_jobs")),
        reserved_budget={},
        metadata={
            "planning_estimates_only": True,
            "cost_basis": (
                "configured_relative_or_imported"
                if config.get("campaign_budget_cost") is not None
                else "unknown"
            ),
            "require_generated_molecule_review": bool(
                config.get("require_generated_review_gate", True)
            ),
            "generated_molecule_review_hours": _optional_float(
                config.get("generated_molecule_review_hours")
            )
            or 1.0,
        },
    )


def _apply_resource_estimates(
    packages: Sequence[CampaignWorkPackage],
    config: Mapping[str, Any],
) -> list[CampaignWorkPackage]:
    require_generated_gate = bool(config.get("require_generated_review_gate", True))
    updated: list[CampaignWorkPackage] = []
    for package in packages:
        approvals = list(package.required_approvals)
        if require_generated_gate and _is_generated_package(package):
            if "generated_molecule_review_gate" not in approvals:
                approvals.append("generated_molecule_review_gate")
        updated.append(package.model_copy(update={"required_approvals": approvals}))
    return updated


def _finalize_plan(plan: CampaignPlan, config: Mapping[str, Any]) -> CampaignPlan:
    schedule = schedule_campaign_work(plan.work_packages)
    budget_summary = compute_campaign_budget_summary(plan)
    budget_summary["usage"] = budget_summary.get("totals", {})
    budget_check = check_budget_constraints(plan, plan.budget)
    stage_gates = _stage_gates_for_plan(plan, budget_check, config)
    warnings = list(plan.warnings)
    warnings.extend(schedule.get("warnings", []))
    return plan.model_copy(
        update={
            "stage_gates": stage_gates,
            "dependency_graph": schedule["dependency_graph"],
            "recommended_sequence": schedule["recommended_sequence"],
            "budget_summary": budget_summary,
            "warnings": _dedupe(warnings),
            "metadata": {
                **plan.metadata,
                "campaign_phases": schedule["phases"],
                "parallel_groups": schedule["parallel_groups"],
                "blocked_work_packages": schedule["blocked_work_packages"],
                "deterministic_campaign_planning_agent": True,
            },
        }
    )


def _stage_gates_for_plan(
    plan: CampaignPlan,
    budget_check: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    if bool(config.get("require_campaign_approval", True)):
        gates.append(build_campaign_approval_gate(plan.campaign_id))
    if bool(config.get("require_generated_review_gate", True)):
        for package in plan.work_packages:
            if _is_generated_package(package):
                gates.append(build_generated_molecule_review_gate(package))
    for package in plan.work_packages:
        if _is_safety_package(package):
            gates.append(build_safety_review_gate(package))
    budget_gate = build_budget_approval_gate(
        campaign_id=plan.campaign_id,
        budget_check=dict(budget_check),
    )
    if budget_gate is not None:
        gates.append(budget_gate)
    return _unique_gates(gates)


def _write_campaign_artifacts(
    *,
    output_dir: Path,
    campaign: Any,
    plan: CampaignPlan,
    memo_markdown: str,
    report_markdown: str,
) -> dict[str, Path]:
    campaign_path = output_dir / "campaign.json"
    plan_path = output_dir / "campaign_plan.json"
    budget_path = output_dir / "campaign_budget.json"
    dependencies_path = output_dir / "campaign_dependencies.json"
    stage_gates_path = output_dir / "campaign_stage_gates.json"
    replan_triggers_path = output_dir / "campaign_replan_triggers.json"
    memo_path = output_dir / "campaign_memo.md"
    report_path = output_dir / "campaign_report.md"
    campaign_path.write_text(
        json.dumps(campaign.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    plan_path.write_text(
        json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    budget_path.write_text(
        json.dumps(plan.budget.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    dependencies_path.write_text(
        json.dumps(plan.dependency_graph, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stage_gates_path.write_text(
        json.dumps(plan.stage_gates, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    replan_triggers_path.write_text(
        json.dumps(plan.replan_triggers, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    memo_path.write_text(memo_markdown, encoding="utf-8")
    report_path.write_text(report_markdown, encoding="utf-8")
    return {
        "campaign": campaign_path,
        "campaign_plan": plan_path,
        "campaign_budget": budget_path,
        "campaign_dependencies": dependencies_path,
        "campaign_stage_gates": stage_gates_path,
        "campaign_replan_triggers": replan_triggers_path,
        "campaign_memo": memo_path,
        "campaign_report": report_path,
    }


def _create_or_load_campaign(store: CampaignStore, campaign: Any) -> None:
    try:
        store.create_campaign(campaign)
    except ValueError:
        store.get_campaign(campaign.campaign_id)


def _project_metadata(context: PipelineContext) -> dict[str, Any]:
    return {
        "project_id": context.config.get("project_id"),
        "name": context.config.get("project_name") or context.config.get("campaign_name"),
        "disease_focus": _disease_focus(context),
        "target_focus": _target_focus(context),
    }


def _program_metadata(context: PipelineContext) -> dict[str, Any]:
    return {
        "program_id": context.config.get("program_id"),
        "name": context.config.get("program_name") or context.config.get("campaign_name"),
        "disease_focus": _disease_focus(context),
        "target_focus": _target_focus(context),
    }


def _disease_focus(context: PipelineContext) -> list[str]:
    if context.disease is not None:
        return [context.disease.canonical_name]
    return [context.disease_input] if context.disease_input else []


def _target_focus(context: PipelineContext) -> list[str]:
    return [target.symbol for target in context.targets if target.symbol]


def _knowledge_graph_paths(context: PipelineContext) -> list[Path]:
    paths: list[Path] = []
    for key in ("knowledge_graph_path", "campaign_knowledge_graph_path"):
        value = context.config.get(key)
        if value:
            paths.append(Path(str(value)))
    raw = context.config.get("hypothesis_generation")
    if isinstance(raw, Mapping):
        artifacts = raw.get("artifact_paths")
        if isinstance(artifacts, Mapping):
            for key in ("knowledge_graph", "graph"):
                if artifacts.get(key):
                    paths.append(Path(str(artifacts[key])))
    return paths


def _hypothesis_ranking(path: Path) -> dict[str, float]:
    output: dict[str, float] = {}
    for item in _records(_load_mapping(path), "hypotheses"):
        hypothesis_id = _optional_str(item.get("hypothesis_id"))
        score = _optional_float(item.get("priority_score"))
        if hypothesis_id is not None and score is not None:
            output[hypothesis_id] = score
    return output


def _model_uncertainty(path: Path) -> dict[str, float]:
    output: dict[str, float] = {}
    for item in _records(_load_mapping(path), "hypotheses"):
        score = _optional_float(item.get("uncertainty_score"))
        if score is None:
            continue
        candidate_ids = _string_list(item.get("linked_candidate_ids"))
        metadata = item.get("metadata")
        if isinstance(metadata, Mapping):
            candidate_ids.extend(_string_list(metadata.get("candidate_ids")))
        for candidate_id in candidate_ids:
            output[candidate_id] = score
    return output


def _active_learning_suggestions(
    context: PipelineContext,
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    path = _optional_artifact_path(
        context,
        direct_keys=(
            "campaign_active_learning_batch_json",
            "active_learning_batch_json",
            "active_learning_batch_path",
        ),
        nested_key="active_learning_batch",
        section="active_learning",
    ) or _existing_output_artifact(output_dir, "active_learning_batch.json")
    payload = _load_optional_mapping(path)
    output: dict[str, dict[str, Any]] = {}
    for suggestion in _records(payload, "suggestions"):
        candidate_id = _optional_str(suggestion.get("candidate_id"))
        if candidate_id is not None:
            output[candidate_id] = dict(suggestion)
    return output


def _review_status(context: PipelineContext) -> dict[str, str]:
    raw = context.config.get("review_status")
    return {str(key): str(value) for key, value in raw.items()} if isinstance(raw, Mapping) else {}


def _graph_contradictions(context: PipelineContext) -> dict[str, float]:
    output: dict[str, float] = {}
    raw = context.config.get("graph_contradictions") or context.config.get("contradiction_reports")
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            score = _optional_float(value)
            if score is not None:
                output[str(key)] = score
    elif isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            key = _optional_str(item.get("work_package_id") or item.get("hypothesis_id"))
            score = _optional_float(item.get("severity_score") or item.get("importance"))
            if key is not None:
                output[key] = score or 0.8
    return output


def _load_optional_mapping(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return _load_mapping(path)


def _load_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _records(payload: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = payload.get(key, [])
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, Mapping):
        return [
            item
            for value in raw.values()
            for item in (value if isinstance(value, list) else [value])
            if isinstance(item, dict)
        ]
    return []


def _is_generated_package(package: CampaignWorkPackage) -> bool:
    text = " ".join(
        [
            package.package_type,
            package.title,
            package.high_level_activity_category,
            *package.required_approvals,
            *(str(value) for value in package.metadata.values()),
        ]
    ).lower()
    return "generated" in text


def _is_safety_package(package: CampaignWorkPackage) -> bool:
    text = " ".join(
        [
            package.title,
            package.description,
            package.high_level_activity_category,
            *package.blocking_reasons,
            *package.warnings,
        ]
    ).lower()
    return "safety" in text or "critical risk" in text


def _unique_gates(gates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for gate in gates:
        gate_id = str(gate.get("gate_id"))
        if gate_id in seen:
            continue
        seen.add(gate_id)
        output.append(gate)
    return output


def _max_work_packages(config: Mapping[str, Any]) -> int:
    try:
        return max(1, int(config.get("max_campaign_work_packages", 50)))
    except (TypeError, ValueError):
        return 50


def _dedupe(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value not in output:
            output.append(value)
    return output


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item)]
    return []


__all__ = ["CampaignPlanningAgent"]
