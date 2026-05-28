from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.agents.scientific_design_planner import (
    DesignPlan,
    DesignPlanValidationError,
    ScientificDesignPlannerAgent,
)
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    Disease,
    EvidenceItem,
    MoleculeCandidate,
    Target,
)


class FakeCodexPlannerProvider:
    def __init__(self, output_json: dict[str, Any], *, status: str = "succeeded") -> None:
        self.output_json = output_json
        self.status = status
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        now = datetime.now(UTC)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status=self.status,  # type: ignore[arg-type]
            output_text=json.dumps(self.output_json, sort_keys=True),
            output_json=self.output_json,
            stdout=json.dumps(self.output_json, sort_keys=True),
            stderr="",
            return_code=0,
            started_at=now,
            completed_at=now,
        )


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )


def _target(symbol: str = "MAOB") -> Target:
    return Target(
        symbol=symbol,
        name="Monoamine oxidase B",
        identifiers={"ensembl": "ENSG00000069535"},
        disease_relevance_score=0.84,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="OT:MAOB",
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Source-backed target association.",
                confidence=0.84,
            )
        ],
    )


def _candidate(name: str = "Rasagiline") -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        chemical_metadata={"canonical_smiles": "C#CCN(C)Cc1ccccc1"},
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id="CHEMBL:MECH:887",
                title="Mechanism record",
                evidence_type="mechanism",
                summary="Source-backed molecule-target evidence.",
                confidence=0.9,
            )
        ],
    )


def _developability() -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="Rasagiline",
        origin="existing",
        structure_available=True,
        canonical_smiles="C#CCN(C)Cc1ccccc1",
        developability_score=0.72,
        triage_recommendation="favorable_hypothesis",
    )


def _valid_plan_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "design_plan_id": "design-plan-1",
        "disease_name": "Parkinson disease",
        "target_priorities": [
            {
                "target_symbol": "MAOB",
                "priority": 0.84,
                "rationale": "Prioritize source-backed target association.",
                "evidence_refs": ["OT:MAOB"],
            }
        ],
        "design_objectives": [
            {
                "objective_id": "obj-maob-analog",
                "target_symbol": "MAOB",
                "objective_type": "target_conditioned_analog_generation",
                "seed_candidate_names": ["Rasagiline"],
                "constraints": {"max_molecular_weight": 500},
            }
        ],
        "seed_strategy": {
            "candidate_names": ["Rasagiline"],
            "source_evidence_refs": ["CHEMBL:MECH:887"],
        },
        "generator_strategy": {"methods": ["selfies_mutation"], "max_rounds": 2},
        "oracle_strategy": {"scores": ["validity", "novelty", "diversity"]},
        "diversity_strategy": {"max_similarity": 0.85},
        "uncertainty_strategy": {"method": "bounded_score_gap"},
        "experiment_readiness_strategy": {"review_queue_only": True},
        "risks": [{"risk": "limited source coverage", "evidence_refs": ["OT:MAOB"]}],
        "constraints": {"no_synthesis_protocols": True},
        "required_followups": [{"type": "review", "target_symbol": "MAOB"}],
        "codex_task_result_id": "codex-scientific-design-plan",
        "metadata": {"planned_by": "codex"},
    }
    payload.update(overrides)
    return payload


def _build_plan(output_json: dict[str, Any]) -> DesignPlan:
    provider = FakeCodexPlannerProvider(output_json)
    plan = ScientificDesignPlannerAgent(provider=provider).build_plan(
        disease=_disease(),
        targets=[_target()],
        existing_candidates=[_candidate()],
        literature_evidence={
            "records": [
                {
                    "source_record_id": "PMID:12345",
                    "title": "Source-backed literature record",
                }
            ]
        },
        developability_assessments=[_developability()],
        experimental_results=[],
        review_decisions=[],
        active_learning_history=[],
        artifact_manifests=[{"artifact_id": "targets", "path": "targets.json"}],
    )
    assert provider.tasks
    assert provider.tasks[0].allowed_commands == []
    assert provider.tasks[0].metadata["planning_mode"] == "scientific_design_v1_1"
    return plan


def test_planner_builds_plan_from_mocked_artifacts() -> None:
    plan = _build_plan(_valid_plan_payload())

    assert plan.design_plan_id == "design-plan-1"
    assert plan.disease_name == "Parkinson disease"
    assert plan.codex_task_result_id == "codex-scientific-design-plan"
    assert plan.target_priorities[0]["target_symbol"] == "MAOB"
    assert plan.metadata["deterministic_validation"]["approved"] is True


def test_fake_target_reference_rejected() -> None:
    payload = _valid_plan_payload(
        target_priorities=[{"target_symbol": "FAKE", "priority": 0.4}],
    )

    with pytest.raises(DesignPlanValidationError, match="unknown target"):
        _build_plan(payload)


def test_fake_citation_rejected() -> None:
    payload = _valid_plan_payload(
        risks=[{"risk": "unsupported citation", "evidence_refs": ["PMID:999999"]}],
    )

    with pytest.raises(DesignPlanValidationError, match="unknown evidence"):
        _build_plan(payload)


def test_unsafe_lab_protocol_rejected() -> None:
    payload = _valid_plan_payload(
        required_followups=[
            {
                "type": "wet_lab",
                "details": "Run a lab protocol with reaction conditions.",
            }
        ],
    )

    with pytest.raises(DesignPlanValidationError, match="protocol"):
        _build_plan(payload)


def test_valid_plan_produces_machine_readable_design_objectives() -> None:
    plan = _build_plan(_valid_plan_payload())

    assert isinstance(plan.design_objectives, list)
    assert plan.design_objectives
    objective = plan.design_objectives[0]
    assert objective["objective_id"] == "obj-maob-analog"
    assert objective["target_symbol"] == "MAOB"
    assert objective["objective_type"] == "target_conditioned_analog_generation"
    assert objective["constraints"] == {"max_molecular_weight": 500}
