from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignObjective,
    CampaignObjectiveType,
    CampaignWorkPackage,
    CampaignWorkPackageType,
)


@dataclass(frozen=True)
class CampaignBuildResult:
    campaign: Campaign
    objectives: list[CampaignObjective]
    work_packages: list[CampaignWorkPackage]


def build_campaign_draft(
    *,
    hypotheses_path: Path | str,
    research_questions_path: Path | str | None = None,
    falsification_criteria_path: Path | str | None = None,
    evidence_gaps_path: Path | str | None = None,
    portfolio_optimization_path: Path | str | None = None,
    active_learning_batch_path: Path | str | None = None,
    review_queue_path: Path | str | None = None,
    experimental_evidence_path: Path | str | None = None,
    model_predictions_path: Path | str | None = None,
    structure_aware_assessments_path: Path | str | None = None,
    knowledge_graph_artifact_paths: Sequence[Path | str] | None = None,
    project_metadata: Mapping[str, Any] | Path | str | None = None,
    program_metadata: Mapping[str, Any] | Path | str | None = None,
    campaign_id: str | None = None,
    name: str | None = None,
) -> CampaignBuildResult:
    """Build a deterministic draft campaign from imported planning artifacts."""

    hypotheses_payload = _load_json(hypotheses_path)
    hypotheses = _records(hypotheses_payload, "hypotheses")
    if not hypotheses:
        raise ValueError("Campaign builder requires at least one hypothesis.")

    evidence_gap_payload = _load_optional_json(evidence_gaps_path)
    evidence_gaps_by_hypothesis = _evidence_gaps_by_hypothesis(evidence_gap_payload)
    portfolio_payload = _load_optional_json(portfolio_optimization_path)
    portfolio_selection_ids = _portfolio_selection_ids(portfolio_payload)
    active_learning_payload = _load_optional_json(active_learning_batch_path)
    review_payload = _load_optional_json(review_queue_path)
    review_decisions_by_hypothesis = _review_decisions_by_hypothesis(review_payload)
    project = _metadata(project_metadata)
    program = _metadata(program_metadata)

    resolved_campaign_id = campaign_id or _stable_id(
        "campaign",
        project.get("project_id"),
        program.get("program_id"),
        *[_string(item.get("hypothesis_id")) for item in hypotheses],
    )
    campaign = Campaign(
        campaign_id=resolved_campaign_id,
        project_id=_optional_string(project.get("project_id")),
        program_id=_optional_string(program.get("program_id")),
        name=name or _campaign_name(project, program),
        description="Draft campaign built from deterministic planning artifacts.",
        disease_focus=_string_list(program.get("disease_focus") or project.get("disease_focus")),
        target_focus=_string_list(program.get("target_focus") or project.get("target_focus")),
        hypothesis_ids=[
            hypothesis_id
            for hypothesis in hypotheses
            if (hypothesis_id := _optional_string(hypothesis.get("hypothesis_id")))
        ],
        portfolio_selection_ids=portfolio_selection_ids,
        status="draft",
        metadata={
            "deterministic_builder": True,
            "source_artifacts": _source_artifacts(
                hypotheses_path=hypotheses_path,
                research_questions_path=research_questions_path,
                falsification_criteria_path=falsification_criteria_path,
                evidence_gaps_path=evidence_gaps_path,
                portfolio_optimization_path=portfolio_optimization_path,
                active_learning_batch_path=active_learning_batch_path,
                review_queue_path=review_queue_path,
                experimental_evidence_path=experimental_evidence_path,
                model_predictions_path=model_predictions_path,
                structure_aware_assessments_path=structure_aware_assessments_path,
                knowledge_graph_artifact_paths=knowledge_graph_artifact_paths,
            ),
        },
    )

    objectives: list[CampaignObjective] = []
    work_packages: list[CampaignWorkPackage] = []
    for hypothesis in hypotheses:
        objective = _objective_for_hypothesis(
            campaign.campaign_id,
            hypothesis,
            evidence_gaps=evidence_gaps_by_hypothesis.get(
                _string(hypothesis.get("hypothesis_id")),
                [],
            ),
            review_decision_ids=review_decisions_by_hypothesis.get(
                _string(hypothesis.get("hypothesis_id")),
                [],
            ),
            portfolio_selection_ids=portfolio_selection_ids,
        )
        objectives.append(objective)
        work_packages.extend(
            _work_packages_for_objective(
                campaign.campaign_id,
                objective,
                hypothesis,
                evidence_gaps=evidence_gaps_by_hypothesis.get(
                    _string(hypothesis.get("hypothesis_id")),
                    [],
                ),
                active_learning_payload=active_learning_payload,
            )
        )

    return CampaignBuildResult(
        campaign=campaign,
        objectives=objectives,
        work_packages=work_packages,
    )


def _objective_for_hypothesis(
    campaign_id: str,
    hypothesis: Mapping[str, Any],
    *,
    evidence_gaps: Sequence[Mapping[str, Any]],
    review_decision_ids: Sequence[str],
    portfolio_selection_ids: Sequence[str],
) -> CampaignObjective:
    hypothesis_id = _string(hypothesis.get("hypothesis_id"))
    objective_type = _objective_type(hypothesis, evidence_gaps)
    evidence_gap_ids = [
        gap_id for gap in evidence_gaps if (gap_id := _optional_string(gap.get("gap_id")))
    ]
    candidate_ids = _candidate_ids(hypothesis)
    metadata = {
        "linked_hypothesis_ids": [hypothesis_id],
        "linked_evidence_gap_ids": evidence_gap_ids,
        "linked_review_decision_ids": list(review_decision_ids),
        "linked_portfolio_selection_ids": list(portfolio_selection_ids),
        "source_hypothesis_type": _optional_string(hypothesis.get("hypothesis_type")),
    }
    if not _objective_has_anchor(metadata):
        raise ValueError(f"Campaign objective for {hypothesis_id} has no source anchor.")
    return CampaignObjective(
        objective_id=_stable_id("campaign-objective", campaign_id, hypothesis_id, objective_type),
        campaign_id=campaign_id,
        name=_objective_name(objective_type, hypothesis),
        objective_type=objective_type,
        linked_hypothesis_ids=[hypothesis_id],
        linked_candidate_ids=candidate_ids,
        success_criteria=_success_criteria(objective_type),
        stop_criteria=_stop_criteria(objective_type),
        priority_weight=_priority_weight(hypothesis, objective_type, evidence_gaps),
        metadata=metadata,
    )


def _work_packages_for_objective(
    campaign_id: str,
    objective: CampaignObjective,
    hypothesis: Mapping[str, Any],
    *,
    evidence_gaps: Sequence[Mapping[str, Any]],
    active_learning_payload: Mapping[str, Any] | None,
) -> list[CampaignWorkPackage]:
    hypothesis_id = objective.linked_hypothesis_ids[0]
    if _is_critical_risk(hypothesis, evidence_gaps):
        return [
            _work_package(
                campaign_id,
                objective,
                hypothesis,
                package_type="developability_review",
                title="Stop and review critical risk",
                description=(
                    "Review critical safety or developability risk before campaign "
                    "advancement."
                ),
                category="risk review",
                blocking_reasons=["stop pending expert risk review"],
                estimated_assay_slots=0,
                warnings=["Critical risk routes to review before additional campaign advancement."],
            )
        ]
    if objective.objective_type == "resolve_contradiction":
        return [
            _work_package(
                campaign_id,
                objective,
                hypothesis,
                package_type="computational_rerun",
                title="Contradiction resolution analysis",
                description="Compare source-backed contradictory records and model context.",
                category="contradiction resolution",
                estimated_compute_units=1.0,
                warnings=["Contradiction resolution does not create new evidence."],
            )
        ]
    if evidence_gaps or objective.objective_type == "close_evidence_gap":
        return [
            _work_package(
                campaign_id,
                objective,
                hypothesis,
                package_type="literature_update",
                title="Evidence gap closure review",
                description="Review missing evidence categories and source-backed context.",
                category="evidence gap review",
                warnings=["Missing evidence routes to review rather than factual invention."],
            )
        ]
    if _is_generated_molecule(hypothesis):
        return [
            _work_package(
                campaign_id,
                objective,
                hypothesis,
                package_type="hypothesis_review",
                title="Generated molecule review gate",
                description=(
                    "Review generated molecule provenance and uncertainty before "
                    "advancement."
                ),
                category="generated hypothesis review",
                required_approvals=["generated_molecule_review_gate"],
                estimated_assay_slots=0,
                warnings=["Generated molecules remain computational hypotheses."],
            )
        ]
    if _has_active_learning_candidate(hypothesis, active_learning_payload):
        return [
            _work_package(
                campaign_id,
                objective,
                hypothesis,
                package_type="active_learning_batch",
                title="Active learning batch review",
                description="Review uncertainty-focused candidate batch membership.",
                category="active learning review",
                estimated_compute_units=1.0,
            )
        ]
    if _requires_assay_triage(hypothesis):
        return [
            _work_package(
                campaign_id,
                objective,
                hypothesis,
                package_type="assay_triage_request",
                title="Assay triage planning request",
                description="Review high-level assay triage need and decision context.",
                category="assay triage planning",
                required_approvals=["assay_triage_approval"],
                estimated_review_hours=0.5,
                estimated_assay_slots=1,
                warnings=["Assay triage packages do not contain procedural experimental detail."],
            )
        ]
    return [
        _work_package(
            campaign_id,
            objective,
            hypothesis,
            package_type="expert_review",
            title=f"Expert review for {hypothesis_id}",
            description="Review deterministic campaign inputs and decision context.",
            category="expert review",
        )
    ]


def _work_package(
    campaign_id: str,
    objective: CampaignObjective,
    hypothesis: Mapping[str, Any],
    *,
    package_type: CampaignWorkPackageType,
    title: str,
    description: str,
    category: str,
    required_approvals: Sequence[str] = (),
    blocking_reasons: Sequence[str] = (),
    warnings: Sequence[str] = (),
    estimated_review_hours: float | None = 1.0,
    estimated_compute_units: float | None = None,
    estimated_assay_slots: int | None = None,
) -> CampaignWorkPackage:
    approvals = list(required_approvals)
    if "campaign_advancement_approval" not in approvals:
        approvals.append("campaign_advancement_approval")
    return CampaignWorkPackage(
        work_package_id=_stable_id(
            "campaign-work-package",
            campaign_id,
            objective.objective_id,
            package_type,
            title,
        ),
        campaign_id=campaign_id,
        objective_ids=[objective.objective_id],
        package_type=package_type,
        title=title,
        description=description,
        linked_candidate_ids=_candidate_ids(hypothesis),
        linked_hypothesis_ids=objective.linked_hypothesis_ids,
        high_level_activity_category=category,
        dependencies=[f"Review objective {objective.objective_id}"],
        required_approvals=approvals,
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=estimated_review_hours,
        estimated_compute_units=estimated_compute_units,
        estimated_assay_slots=estimated_assay_slots,
        status="proposed",
        blocking_reasons=list(blocking_reasons),
        warnings=[
            "Work package is a high-level planning unit.",
            *warnings,
        ],
        metadata={
            "deterministic_builder": True,
            "source_hypothesis_id": objective.linked_hypothesis_ids[0],
        },
    )


def _load_json(path: Path | str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _load_optional_json(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return _load_json(path)


def _records(payload: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if payload is None:
        return []
    raw = payload.get(key, [])
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [
            item
            for value in raw.values()
            for item in (value if isinstance(value, list) else [value])
            if isinstance(item, dict)
        ]
    return []


def _evidence_gaps_by_hypothesis(
    payload: Mapping[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    if payload is None:
        return output
    raw = payload.get("evidence_gaps", [])
    if isinstance(raw, Mapping):
        for hypothesis_id, gaps in raw.items():
            output[str(hypothesis_id)] = [
                gap for gap in gaps if isinstance(gap, dict)
            ] if isinstance(gaps, list) else []
        return output
    if isinstance(raw, list):
        for gap in raw:
            if not isinstance(gap, dict):
                continue
            hypothesis_id = _optional_string(gap.get("hypothesis_id"))
            if hypothesis_id:
                output.setdefault(hypothesis_id, []).append(gap)
    return output


def _portfolio_selection_ids(payload: Mapping[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    ids: list[str] = []
    recommended = _optional_string(payload.get("recommended_selection_id"))
    if recommended:
        ids.append(recommended)
    for selection in _records(payload, "selections"):
        selection_id = _optional_string(selection.get("selection_id"))
        if selection_id and selection_id not in ids:
            ids.append(selection_id)
    return ids


def _review_decisions_by_hypothesis(
    payload: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    if payload is None:
        return output
    records = [
        *_records(payload, "review_items"),
        *_records(payload, "review_decisions"),
        *_records(payload, "reviews"),
    ]
    for record in records:
        decision_id = (
            _optional_string(record.get("decision_id"))
            or _optional_string(record.get("review_id"))
            or _optional_string(record.get("review_item_id"))
        )
        hypothesis_ids = _string_list(record.get("hypothesis_ids"))
        hypothesis_id = _optional_string(record.get("hypothesis_id"))
        if hypothesis_id:
            hypothesis_ids.append(hypothesis_id)
        for item in hypothesis_ids:
            if decision_id:
                output.setdefault(item, []).append(decision_id)
    return output


def _metadata(value: Mapping[str, Any] | Path | str | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return _load_json(value)


def _source_artifacts(**paths: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in paths.items():
        if value is None:
            continue
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | Path):
            output[key] = [str(item) for item in value]
        else:
            output[key] = str(value)
    return output


def _objective_type(
    hypothesis: Mapping[str, Any],
    evidence_gaps: Sequence[Mapping[str, Any]],
) -> CampaignObjectiveType:
    hypothesis_type = _string(hypothesis.get("hypothesis_type")).lower()
    if hypothesis_type == "assay_contradiction" or hypothesis.get("contradicting_relation_ids"):
        return "resolve_contradiction"
    if _is_critical_risk(hypothesis, evidence_gaps):
        return "reduce_risk"
    if evidence_gaps or hypothesis_type == "evidence_gap":
        return "close_evidence_gap"
    if hypothesis_type == "portfolio_decision":
        return "portfolio_decision"
    if hypothesis_type == "active_learning":
        return "learn_from_uncertainty"
    if "developability" in hypothesis_type:
        return "improve_developability"
    return "validate_hypothesis"


def _objective_name(objective_type: str, hypothesis: Mapping[str, Any]) -> str:
    title = _optional_string(hypothesis.get("title")) or _string(hypothesis.get("hypothesis_id"))
    labels = {
        "validate_hypothesis": "Validate hypothesis",
        "resolve_contradiction": "Resolve contradiction",
        "close_evidence_gap": "Close evidence gap",
        "reduce_risk": "Reduce risk",
        "improve_developability": "Improve developability",
        "learn_from_uncertainty": "Learn from uncertainty",
        "portfolio_decision": "Support portfolio decision",
    }
    return f"{labels.get(objective_type, 'Review campaign objective')}: {title}"


def _priority_weight(
    hypothesis: Mapping[str, Any],
    objective_type: str,
    evidence_gaps: Sequence[Mapping[str, Any]],
) -> float:
    if objective_type in {"resolve_contradiction", "reduce_risk"}:
        return 0.95
    if any(_string(gap.get("severity")).lower() in {"high", "critical"} for gap in evidence_gaps):
        return 0.85
    raw_score = hypothesis.get("priority_score")
    score = float(raw_score) if isinstance(raw_score, int | float) else 0.7
    return min(1.0, max(0.0, score))


def _success_criteria(objective_type: str) -> list[str]:
    if objective_type == "resolve_contradiction":
        return ["Contradictory source-backed records are reviewed and decision impact is recorded."]
    if objective_type == "close_evidence_gap":
        return ["Evidence gap is mapped to source-backed follow-up planning context."]
    if objective_type == "reduce_risk":
        return ["Risk is reviewed before any campaign advancement decision."]
    return ["Objective is reviewed against deterministic campaign artifacts."]


def _stop_criteria(objective_type: str) -> list[str]:
    if objective_type == "reduce_risk":
        return ["Critical safety or developability concern remains unresolved."]
    if objective_type == "resolve_contradiction":
        return ["Contradiction remains unresolved after expert review."]
    return ["Required source-backed context is unavailable or rejected in review."]


def _candidate_ids(hypothesis: Mapping[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in (
        "linked_candidate_ids",
        "molecule_entity_ids",
        "generated_molecule_entity_ids",
        "candidate_ids",
    ):
        for candidate_id in _string_list(hypothesis.get(key)):
            if candidate_id not in candidates:
                candidates.append(candidate_id)
    return candidates


def _is_generated_molecule(hypothesis: Mapping[str, Any]) -> bool:
    hypothesis_type = _string(hypothesis.get("hypothesis_type")).lower()
    return hypothesis_type == "generated_molecule" or bool(
        _string_list(hypothesis.get("generated_molecule_entity_ids"))
    )


def _is_critical_risk(
    hypothesis: Mapping[str, Any],
    evidence_gaps: Sequence[Mapping[str, Any]],
) -> bool:
    text = " ".join(
        [
            _string(hypothesis.get("hypothesis_type")),
            *(_string(item) for item in _string_list(hypothesis.get("warnings"))),
            *(str(value) for value in _as_mapping(hypothesis.get("metadata")).values()),
            *(
                " ".join(str(value) for value in gap.values())
                for gap in evidence_gaps
            ),
        ]
    ).lower()
    return (
        ("critical" in text and ("risk" in text or "safety" in text or "developability" in text))
        or "critical_safety" in text
        or "critical_developability" in text
    )


def _has_active_learning_candidate(
    hypothesis: Mapping[str, Any],
    payload: Mapping[str, Any] | None,
) -> bool:
    if payload is None:
        return False
    candidate_ids = set(_candidate_ids(hypothesis))
    active_ids = set(_string_list(payload.get("candidate_ids")))
    for suggestion in _records(payload, "suggestions"):
        candidate_id = _optional_string(suggestion.get("candidate_id"))
        if candidate_id:
            active_ids.add(candidate_id)
    return bool(candidate_ids & active_ids)


def _requires_assay_triage(hypothesis: Mapping[str, Any]) -> bool:
    metadata = _as_mapping(hypothesis.get("metadata"))
    return bool(
        hypothesis.get("requires_assay_triage")
        or metadata.get("requires_assay_triage")
        or "assay_triage_request" in _string_list(metadata.get("campaign_package_types"))
    )


def _objective_has_anchor(metadata: Mapping[str, Any]) -> bool:
    return any(
        metadata.get(key)
        for key in (
            "linked_hypothesis_ids",
            "linked_evidence_gap_ids",
            "linked_review_decision_ids",
            "linked_portfolio_selection_ids",
        )
    )


def _campaign_name(project: Mapping[str, Any], program: Mapping[str, Any]) -> str:
    return (
        _optional_string(project.get("name"))
        or _optional_string(program.get("name"))
        or "Draft research campaign"
    )


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _optional_string(value: Any) -> str | None:
    text = _string(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [_string(item) for item in value if _string(item)]
    return []


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_string(part) for part in parts if _string(part)) or prefix
    return f"{prefix}:{uuid5(NAMESPACE_URL, raw).hex[:12]}"


__all__ = ["CampaignBuildResult", "build_campaign_draft"]
