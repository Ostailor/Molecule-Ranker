from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.campaigns.builder import build_campaign_draft
from molecule_ranker.campaigns.schemas import contains_procedural_lab_detail


def test_builder_builds_campaign_from_hypotheses(tmp_path: Path) -> None:
    result = build_campaign_draft(
        hypotheses_path=_write_json(
            tmp_path / "hypotheses.json",
            {"hypotheses": [_hypothesis("hypothesis:mechanism", "mechanism")]},
        ),
        research_questions_path=_write_json(
            tmp_path / "research_questions.json",
            {"questions": []},
        ),
        falsification_criteria_path=_write_json(
            tmp_path / "falsification_criteria.json", {"falsification_criteria": []}
        ),
        evidence_gaps_path=_write_json(tmp_path / "evidence_gaps.json", {"evidence_gaps": []}),
        portfolio_optimization_path=_write_json(
            tmp_path / "portfolio_optimization.json",
            {
                "optimization_run_id": "portfolio-run-1",
                "recommended_selection_id": "selection-1",
                "selections": [
                    {"selection_id": "selection-1", "selected_candidate_ids": ["candidate:1"]}
                ],
            },
        ),
        active_learning_batch_path=_write_json(
            tmp_path / "active_learning_batch.json",
            {"batch_id": "al-1", "candidate_ids": ["candidate:1"]},
        ),
        review_queue_path=_write_json(tmp_path / "review_queue.json", {"review_items": []}),
        experimental_evidence_path=_write_json(
            tmp_path / "experimental_evidence.json", {"assay_results": []}
        ),
        model_predictions_path=_write_json(
            tmp_path / "model_predictions.json", {"predictions": []}
        ),
        structure_aware_assessments_path=_write_json(
            tmp_path / "structure_aware_assessments.json", {"assessments": []}
        ),
        knowledge_graph_artifact_paths=[
            _write_json(tmp_path / "knowledge_graph.json", {"graph_id": "kg-1"})
        ],
        project_metadata={"project_id": "project-1", "name": "Project campaign"},
        program_metadata={
            "program_id": "program-1",
            "disease_focus": ["PD"],
            "target_focus": ["MAOB"],
        },
    )

    assert result.campaign.status == "draft"
    assert result.campaign.project_id == "project-1"
    assert result.campaign.program_id == "program-1"
    assert result.campaign.hypothesis_ids == ["hypothesis:mechanism"]
    assert result.campaign.portfolio_selection_ids == ["selection-1"]
    assert result.objectives
    assert result.work_packages
    assert all(_objective_has_anchor(objective.metadata) for objective in result.objectives)


def test_builder_adds_review_gate_for_generated_molecule(tmp_path: Path) -> None:
    result = build_campaign_draft(
        hypotheses_path=_write_json(
            tmp_path / "hypotheses.json",
            {
                "hypotheses": [
                    _hypothesis(
                        "hypothesis:generated",
                        "generated_molecule",
                        generated_molecule_entity_ids=["generated:1"],
                    )
                ]
            },
        )
    )

    packages = [
        package
        for package in result.work_packages
        if "hypothesis:generated" in package.linked_hypothesis_ids
    ]
    assert packages
    assert all(
        "generated_molecule_review_gate" in package.required_approvals
        for package in packages
    )
    assert all(package.estimated_assay_slots in {None, 0} for package in packages)


def test_builder_creates_contradiction_resolution_package(tmp_path: Path) -> None:
    result = build_campaign_draft(
        hypotheses_path=_write_json(
            tmp_path / "hypotheses.json",
            {
                "hypotheses": [
                    _hypothesis(
                        "hypothesis:contradiction",
                        "assay_contradiction",
                        contradicting_relation_ids=["relation:conflict"],
                    )
                ]
            },
        )
    )

    objective = result.objectives[0]
    package_types = {package.package_type for package in result.work_packages}
    assert objective.objective_type == "resolve_contradiction"
    assert objective.priority_weight >= 0.9
    assert "computational_rerun" in package_types
    assert any("contradiction" in package.title.lower() for package in result.work_packages)


def test_builder_creates_stop_review_package_for_critical_risk(tmp_path: Path) -> None:
    result = build_campaign_draft(
        hypotheses_path=_write_json(
            tmp_path / "hypotheses.json",
            {
                "hypotheses": [
                    _hypothesis(
                        "hypothesis:risk",
                        "developability_risk",
                        warnings=["critical safety risk"],
                        metadata={"risk_severity": "critical"},
                    )
                ]
            },
        )
    )

    packages = result.work_packages
    assert any(package.package_type == "developability_review" for package in packages)
    assert all(package.package_type != "assay_triage_request" for package in packages)
    assert any("stop" in " ".join(package.blocking_reasons).lower() for package in packages)


def test_builder_creates_evidence_gap_package_and_omits_protocol_text(tmp_path: Path) -> None:
    result = build_campaign_draft(
        hypotheses_path=_write_json(
            tmp_path / "hypotheses.json",
            {"hypotheses": [_hypothesis("hypothesis:gap", "evidence_gap")]},
        ),
        evidence_gaps_path=_write_json(
            tmp_path / "evidence_gaps.json",
            {
                "evidence_gaps": {
                    "hypothesis:gap": [
                        {
                            "gap_id": "gap-1",
                            "hypothesis_id": "hypothesis:gap",
                            "gap_type": "missing_direct_experimental_result",
                            "severity": "high",
                            "description": "Missing direct imported evidence.",
                        }
                    ]
                }
            },
        ),
    )

    assert any(package.package_type == "literature_update" for package in result.work_packages)
    all_text = json.dumps(
        {
            "campaign": result.campaign.model_dump(mode="json"),
            "objectives": [objective.model_dump(mode="json") for objective in result.objectives],
            "work_packages": [
                package.model_dump(mode="json") for package in result.work_packages
            ],
        }
    )
    assert not contains_procedural_lab_detail(all_text)


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _hypothesis(
    hypothesis_id: str,
    hypothesis_type: str,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "hypothesis_id": hypothesis_id,
        "hypothesis_type": hypothesis_type,
        "title": hypothesis_id,
        "statement": "Graph-backed campaign planning hypothesis.",
        "priority_score": 0.7,
        "molecule_entity_ids": ["candidate:1"],
        "source_artifact_ids": ["artifact:hypotheses"],
        "supporting_relation_ids": ["relation:support"],
        "warnings": [],
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def _objective_has_anchor(metadata: dict[str, object]) -> bool:
    return bool(
        metadata.get("linked_evidence_gap_ids")
        or metadata.get("linked_review_decision_ids")
        or metadata.get("linked_portfolio_selection_ids")
        or metadata.get("linked_hypothesis_ids")
    )
