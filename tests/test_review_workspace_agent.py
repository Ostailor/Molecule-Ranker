from __future__ import annotations

import json

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.review_workspace import ReviewWorkspaceAgent
from molecule_ranker.review.workspace import ReviewWorkspaceStore
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    Disease,
    EvidenceItem,
    MoleculeCandidate,
    ScoreBreakdown,
    Target,
)


def _context(tmp_path, *, enable_review_workflow: bool) -> PipelineContext:
    disease = Disease(input_name="PD", canonical_name="Parkinson disease")
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[
            EvidenceItem(
                source="OpenTargets",
                source_record_id="target-1",
                title="Target evidence",
                evidence_type="target",
                summary="Target evidence.",
                confidence=0.8,
            )
        ],
    )
    score = ScoreBreakdown(
        disease_target_relevance=0.8,
        molecule_target_evidence=0.75,
        mechanism_plausibility=0.7,
        clinical_precedence=0.5,
        safety_prior=0.7,
        data_quality=0.8,
        novelty_or_repurposing_value=0.4,
        literature_quality=0.6,
        developability_score=0.7,
        final_score=0.76,
        confidence=0.72,
        explanation="Evidence-backed candidate.",
    )
    candidate = MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        chemical_metadata={"canonical_smiles": "C#CCN1CCC2=CC=CC=C21"},
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id="mol-1",
                title="Molecule evidence",
                evidence_type="activity",
                summary="Molecule target evidence.",
                confidence=0.8,
            )
        ],
        score=0.76,
        score_breakdown=score,
        developability_assessment=DevelopabilityAssessment(
            molecule_name="Rasagiline",
            origin="existing",
            structure_available=True,
            canonical_smiles="C#CCN1CCC2=CC=CC=C21",
            developability_score=0.7,
            triage_recommendation="favorable_hypothesis",
            metadata={"risk_level": "low"},
        ),
    )
    return PipelineContext(
        disease_input="PD",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        config={
            "enable_review_workflow": enable_review_workflow,
            "review_db_path": tmp_path / "review.sqlite",
            "results_dir": str(tmp_path / "results"),
            "reviewer_id": "expert-1",
            "reviewer_name": "Local Reviewer",
            "reviewer_role": "medicinal_chemist",
            "max_review_items": 100,
            "include_generated_in_review": True,
            "generated_high_priority_allowed": False,
            "review_priority_policy": "conservative",
        },
    )


def test_review_workspace_agent_disabled_mode_only_appends_trace(tmp_path):
    context = _context(tmp_path, enable_review_workflow=False)

    updated = ReviewWorkspaceAgent().run(context)

    assert "review_workspace_id" not in updated.config
    assert "review_queue_summary" not in updated.config
    assert not (tmp_path / "review.sqlite").exists()
    assert updated.traces[-1].agent_name == "ReviewWorkspaceAgent"
    assert updated.traces[-1].metadata["enabled"] is False


def test_review_workspace_agent_enabled_creates_persisted_workspace_and_queue_artifact(tmp_path):
    context = _context(tmp_path, enable_review_workflow=True)

    updated = ReviewWorkspaceAgent().run(context)

    workspace_id = updated.config["review_workspace_id"]
    output_dir = tmp_path / "results" / "parkinson-disease"
    queue_path = output_dir / "review_queue.json"
    store = ReviewWorkspaceStore(tmp_path / "review.sqlite")
    persisted = store.get_workspace(workspace_id)
    queue_payload = json.loads(queue_path.read_text())
    trace = updated.traces[-1]

    assert queue_path.exists()
    assert persisted.workspace_id == workspace_id
    assert persisted.review_items[0].candidate_name == "Rasagiline"
    assert any(event.event_type == "workspace_created" for event in persisted.audit_events)
    assert updated.config["review_queue_summary"]["review_item_count"] == 1
    assert sum(updated.config["review_queue_summary"]["priority_distribution"].values()) == 1
    assert queue_payload["workspace_id"] == workspace_id
    assert queue_payload["review_items"][0]["candidate_name"] == "Rasagiline"
    assert trace.agent_name == "ReviewWorkspaceAgent"
    assert trace.metadata["workspace_id"] == workspace_id
    assert trace.metadata["review_item_count"] == 1
    assert sum(trace.metadata["priority_distribution"].values()) == 1
    assert trace.metadata["generated_included"] is True
    assert trace.metadata["reviewer"]["reviewer_id"] == "expert-1"


def test_review_workspace_agent_dashboard_option_creates_static_dashboard(tmp_path):
    context = _context(tmp_path, enable_review_workflow=True)
    dashboard_dir = tmp_path / "dashboard"
    context.config["generate_review_dashboard"] = True
    context.config["review_dashboard_dir"] = dashboard_dir

    updated = ReviewWorkspaceAgent().run(context)

    assert (dashboard_dir / "index.html").exists()
    assert (dashboard_dir / "queue.html").exists()
    assert updated.config["review_dashboard_path"] == str(dashboard_dir)
    assert updated.traces[-1].metadata["review_dashboard_path"] == str(dashboard_dir)
