from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.agents import CampaignPlanningAgent
from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.config import RankerConfig
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator


def test_campaign_planning_agent_disabled_noop(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Disease A",
        output_dir=tmp_path,
        config={"enable_campaign_planning": False, "results_dir": str(tmp_path)},
    )

    result = CampaignPlanningAgent().run(context)

    assert "campaign_planning" not in result.config
    assert not (tmp_path / "campaign_plan.json").exists()
    assert result.traces[-1].agent_name == "CampaignPlanningAgent"
    assert result.traces[-1].metadata["enabled"] is False


def test_campaign_planning_agent_enabled_writes_plan_and_memo(tmp_path: Path) -> None:
    context = _context(tmp_path)

    result = CampaignPlanningAgent().run(context)
    metadata = result.config["campaign_planning"]
    plan_path = Path(metadata["artifact_paths"]["campaign_plan"])
    memo_path = Path(metadata["artifact_paths"]["campaign_memo"])
    expected_artifacts = {
        "campaign",
        "campaign_plan",
        "campaign_budget",
        "campaign_dependencies",
        "campaign_stage_gates",
        "campaign_replan_triggers",
        "campaign_memo",
        "campaign_report",
    }

    assert expected_artifacts.issubset(metadata["artifact_paths"])
    for artifact_name in expected_artifacts:
        assert Path(metadata["artifact_paths"][artifact_name]).exists(), artifact_name
    assert plan_path.exists()
    assert memo_path.exists()
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["campaign_id"].startswith("campaign")
    assert payload["work_packages"]
    assert payload["stage_gates"]
    assert metadata["saved"] is True
    assert "CampaignPlanningAgent" in result.traces[-1].agent_name
    assert "Campaign Memo" in memo_path.read_text(encoding="utf-8")


def test_campaign_report_includes_budget_gates_and_required_sections(tmp_path: Path) -> None:
    result = CampaignPlanningAgent().run(_context(tmp_path))

    report = Path(
        result.config["campaign_planning"]["artifact_paths"]["campaign_report"]
    ).read_text(encoding="utf-8")

    for section in [
        "Campaign Summary",
        "Objectives",
        "Work Packages",
        "Budget and Resource Use",
        "Dependency Graph",
        "Recommended Sequence",
        "Stage Gates and Approvals",
        "Replan Triggers",
        "Risks and Uncertainty",
        "Expected Learning Value",
        "Limitations",
    ]:
        assert f"## {section}" in report
    assert "assay_slots" in report
    assert "generated_molecule_review" in report
    assert "campaign plan is research-management guidance" in report.lower()
    assert "not a lab protocol" in report.lower()
    assert "selected candidates are not proven active/safe/effective" in report.lower()


def test_campaign_artifact_report_has_no_procedural_lab_details(tmp_path: Path) -> None:
    result = CampaignPlanningAgent().run(_context(tmp_path))

    combined = "\n".join(
        Path(result.config["campaign_planning"]["artifact_paths"][name]).read_text(
            encoding="utf-8"
        )
        for name in [
            "campaign_report",
            "campaign_memo",
            "campaign_dependencies",
            "campaign_stage_gates",
        ]
    ).lower()

    forbidden = ["reagent", "concentration", "incubat", "mg/kg", "temperature", "37 c"]
    assert not any(term in combined for term in forbidden)
    assert "synthesis route" not in combined


def test_campaign_planning_agent_enforces_budget_constraints(tmp_path: Path) -> None:
    context = _context(tmp_path, budget_assay_slots=0, budget_review_hours=2.0)

    result = CampaignPlanningAgent().run(context)
    payload = json.loads(
        Path(result.config["campaign_planning"]["artifact_paths"]["campaign_plan"]).read_text(
            encoding="utf-8"
        )
    )

    assert payload["budget_summary"]["usage"]["assay_slots"] == 0
    assert any(
        "budget constraints" in warning.lower() for warning in payload["warnings"]
    )
    assert payload["metadata"]["excluded_work_package_ids"]


def test_campaign_planning_agent_adds_generated_review_gate(tmp_path: Path) -> None:
    context = _context(tmp_path)

    result = CampaignPlanningAgent().run(context)
    payload = json.loads(
        Path(result.config["campaign_planning"]["artifact_paths"]["campaign_plan"]).read_text(
            encoding="utf-8"
        )
    )
    gate_types = {gate["gate_type"] for gate in payload["stage_gates"]}

    assert "generated_molecule_review" in gate_types


def test_campaign_planning_memo_contains_no_protocol_text(tmp_path: Path) -> None:
    context = _context(tmp_path)

    result = CampaignPlanningAgent().run(context)
    memo = Path(result.config["campaign_planning"]["artifact_paths"]["campaign_memo"]).read_text(
        encoding="utf-8"
    )

    forbidden = ["protocol", "reagent", "concentration", "incubat", "mg/kg", "temperature"]
    assert not any(term in memo.lower() for term in forbidden)
    assert "synthesis route" not in memo.lower()


def test_campaign_planning_agent_pipeline_placement() -> None:
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(enable_campaign_planning=True)
    )
    names = [agent.name for agent in orchestrator.agents]

    assert names.index("HypothesisGenerationAgent") < names.index("CampaignPlanningAgent")
    assert names.index("PortfolioOptimizationAgent") < names.index("CampaignPlanningAgent")
    assert names.index("CampaignPlanningAgent") < names.index("ReviewWorkspaceAgent")
    assert names.index("CampaignPlanningAgent") < names.index("ReportWriterAgent")


def _context(
    tmp_path: Path,
    *,
    budget_assay_slots: int | None = 1,
    budget_review_hours: float | None = 5.0,
) -> PipelineContext:
    artifact_paths = _write_campaign_inputs(tmp_path)
    return PipelineContext(
        disease_input="Disease A",
        output_dir=tmp_path,
        config={
            "results_dir": str(tmp_path),
            "enable_campaign_planning": True,
            "campaign_name": "Disease A campaign",
            "project_id": "project-a",
            "program_id": "program-a",
            "campaign_budget_assay_slots": budget_assay_slots,
            "campaign_budget_review_hours": budget_review_hours,
            "campaign_budget_compute_units": 5.0,
            "campaign_budget_cost": None,
            "require_campaign_approval": True,
            "require_generated_review_gate": True,
            "campaign_planning_strategy": "balanced",
            "max_campaign_work_packages": 50,
            "hypothesis_generation": {
                "enabled": True,
                "artifact_paths": {
                    "hypotheses": str(artifact_paths["hypotheses"]),
                    "research_questions": str(artifact_paths["research_questions"]),
                    "falsification_criteria": str(artifact_paths["falsification_criteria"]),
                    "evidence_gaps": str(artifact_paths["evidence_gaps"]),
                },
            },
            "portfolio_optimization": {
                "enabled": True,
                "artifact_paths": {
                    "portfolio_optimization": str(artifact_paths["portfolio_optimization"])
                },
            },
            "active_learning_batch_json": str(artifact_paths["active_learning_batch"]),
        },
    )


def _write_campaign_inputs(tmp_path: Path) -> dict[str, Path]:
    hypotheses = tmp_path / "hypotheses.json"
    research_questions = tmp_path / "research_questions.json"
    falsification_criteria = tmp_path / "falsification_criteria.json"
    evidence_gaps = tmp_path / "evidence_gaps.json"
    portfolio_optimization = tmp_path / "portfolio_optimization.json"
    active_learning_batch = tmp_path / "active_learning_batch.json"

    hypotheses.write_text(
        json.dumps(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "hypothesis-existing",
                        "hypothesis_type": "mechanism",
                        "title": "Existing candidate planning hypothesis",
                        "statement": "Source-backed planning hypothesis.",
                        "priority_score": 0.8,
                        "uncertainty_score": 0.4,
                        "metadata": {"candidate_ids": ["candidate-existing"]},
                    },
                    {
                        "hypothesis_id": "hypothesis-generated",
                        "hypothesis_type": "generated_molecule",
                        "title": "Generated molecule planning hypothesis",
                        "statement": "Generated molecule remains a computational hypothesis.",
                        "priority_score": 0.9,
                        "uncertainty_score": 0.8,
                        "metadata": {
                            "candidate_ids": ["candidate-generated"],
                            "generated_molecule": True,
                        },
                    },
                    {
                        "hypothesis_id": "hypothesis-assay",
                        "hypothesis_type": "mechanism",
                        "title": "Assay triage planning hypothesis",
                        "statement": "Candidate comparison planning hypothesis.",
                        "priority_score": 0.7,
                        "uncertainty_score": 0.5,
                        "metadata": {
                            "candidate_ids": ["candidate-assay"],
                            "requires_assay_triage": True,
                        },
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    research_questions.write_text('{"questions": []}\n', encoding="utf-8")
    falsification_criteria.write_text('{"falsification_criteria": []}\n', encoding="utf-8")
    evidence_gaps.write_text(
        json.dumps(
            {
                "evidence_gaps": [
                    {
                        "gap_id": "gap-existing",
                        "hypothesis_id": "hypothesis-existing",
                        "gap_type": "missing_literature",
                        "severity": "medium",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    portfolio_optimization.write_text(
        json.dumps(
            {
                "optimization_run": {
                    "run_id": "portfolio-run-a",
                    "selections": [
                        {
                            "selection_id": "portfolio-selection-a",
                            "selected_candidate_ids": [
                                "candidate-existing",
                                "candidate-generated",
                                "candidate-assay",
                            ],
                        }
                    ],
                },
                "selected_candidate_ids": [
                    "candidate-existing",
                    "candidate-generated",
                    "candidate-assay",
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    active_learning_batch.write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "suggestion_id": "active-learning-1",
                        "candidate_id": "candidate-generated",
                        "hypothesis_id": "hypothesis-generated",
                        "expected_learning_value": 0.9,
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "hypotheses": hypotheses,
        "research_questions": research_questions,
        "falsification_criteria": falsification_criteria,
        "evidence_gaps": evidence_gaps,
        "portfolio_optimization": portfolio_optimization,
        "active_learning_batch": active_learning_batch,
    }
