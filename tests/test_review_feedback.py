from __future__ import annotations

from molecule_ranker.review import Reviewer
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.feedback import FeedbackStore, apply_feedback_to_review_item
from molecule_ranker.review.queue_builder import build_review_workspace
from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    EvidenceItem,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
    Target,
)


def _item() -> ReviewItem:
    return ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        score=0.65,
        confidence=0.7,
        evidence_summary={
            "score_breakdown": {"final_score": 0.65, "confidence": 0.7},
            "target_evidence_count": 1,
            "molecule_evidence_count": 1,
            "literature_claim_counts": {"supports": 1, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 0,
            "developability_risk_level": "low",
            "generated_score": None,
        },
        literature_summary={"claim_counts": {"supports": 1}},
        developability_summary={"risk_level": "low"},
        generation_summary=None,
        risk_flags=[],
        warnings=[],
        priority_bucket="medium_priority",
        review_status="pending",
        metadata={"inchikey": "ABCDEF-GHIJKL-M"},
    )


def _workspace_with_decisions() -> ReviewWorkspace:
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        review_items=[_item()],
    )
    reviewer = Reviewer(reviewer_id="expert-1", role="medicinal_chemist")
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=workspace.review_items[0].review_item_id,
        reviewer=reviewer,
        decision="accept_for_followup",
        rationale="Strong target rationale for expert review.",
        confidence=0.8,
        decision_factors=["strong_target_rationale"],
    )
    return workspace


def _ranking_run() -> RankingRun:
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.85,
        evidence=[
            EvidenceItem(
                source="OpenTargets",
                title="Target evidence",
                evidence_type="genetic_association",
                summary="Disease target association.",
                confidence=0.8,
            )
        ],
    )
    candidate = MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887", "inchikey": "ABCDEF-GHIJKL-M"},
        known_targets=["MAOB"],
        chemical_metadata={"canonical_smiles": "C#CCN1CCC2=CC=CC=C21"},
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                title="Molecule evidence",
                evidence_type="activity",
                summary="Molecule target evidence.",
                confidence=0.8,
            )
        ],
        score=0.65,
        score_breakdown=ScoreBreakdown(
            disease_target_relevance=0.7,
            molecule_target_evidence=0.7,
            mechanism_plausibility=0.6,
            clinical_precedence=0.4,
            safety_prior=0.5,
            data_quality=0.7,
            novelty_or_repurposing_value=0.3,
            literature_quality=0.5,
            developability_score=0.6,
            final_score=0.65,
            confidence=0.7,
            explanation="Moderate candidate.",
        ),
    )
    return RankingRun(
        disease=Disease(input_name="PD", canonical_name="Parkinson disease"),
        targets=[target],
        candidates=[candidate],
        traces=[AgentTrace(agent_name="test", input_summary="in", output_summary="out")],
    )


def test_feedback_store_saves_decisions_and_retrieves_by_candidate(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    workspace = _workspace_with_decisions()

    saved = store.save_from_workspace(workspace)
    by_name = store.query(candidate_name="Rasagiline")
    by_inchikey = store.query(inchikey="ABCDEF-GHIJKL-M")
    by_target = store.query(target="MAOB")
    by_disease = store.query(disease="Parkinson disease")

    assert len(saved) == 1
    assert by_name[0].candidate_id == "CHEMBL887"
    assert by_inchikey[0].candidate_name == "Rasagiline"
    assert by_target[0].tags == ["strong_target_rationale"]
    assert by_disease[0].source_workspace_id == workspace.workspace_id
    assert by_name[0].metadata["source_label"] == "expert review feedback"


def test_feedback_export_import_round_trip(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    workspace = _workspace_with_decisions()
    store.save_from_workspace(workspace)
    output = tmp_path / "feedback.json"

    store.export_json(output)
    imported = FeedbackStore(tmp_path / "imported.sqlite").import_json(output)

    assert len(imported) == 1
    assert imported[0].candidate_name == "Rasagiline"


def test_feedback_does_not_create_evidence_item():
    workspace = _workspace_with_decisions()
    candidate = _ranking_run().candidates[0]
    before_count = len(candidate.evidence)

    feedback = FeedbackStore.in_memory_from_workspace(workspace)

    assert len(candidate.evidence) == before_count
    assert feedback[0].metadata["source_label"] == "expert review feedback"
    assert not isinstance(feedback[0], EvidenceItem)


def test_feedback_affects_priority_only_when_enabled(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    store.save_from_workspace(_workspace_with_decisions())
    run = _ranking_run()

    disabled = build_review_workspace(
        run,
        config={"run_id": "disabled", "enable_feedback_prior": False},
    )
    enabled = build_review_workspace(
        run,
        config={
            "run_id": "enabled",
            "enable_feedback_prior": True,
            "feedback_db_path": str(store.db_path),
            "feedback_weight": 0.05,
        },
    )

    assert disabled.review_items[0].priority_bucket == "medium_priority"
    assert enabled.review_items[0].priority_bucket == "high_priority"
    assert enabled.review_items[0].metadata["feedback_context"]["source_label"] == (
        "expert review feedback"
    )
    assert "expert review feedback" in enabled.review_items[0].warnings


def test_conflicting_feedback_is_reported():
    item = _item()
    workspace = _workspace_with_decisions()
    reviewer = Reviewer(reviewer_id="expert-2", role="pharmacologist")
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=item.review_item_id,
        reviewer=reviewer,
        decision="reject",
        rationale="Conflicting expert concern.",
        confidence=0.9,
        decision_factors=["safety_risk"],
    )
    feedback = FeedbackStore.in_memory_from_workspace(workspace)

    updated = apply_feedback_to_review_item(
        item,
        feedback,
        enable_feedback_prior=True,
        feedback_weight=0.05,
    )

    assert updated.priority_bucket == "needs_review"
    assert updated.metadata["feedback_context"]["conflicting_feedback"] is True
    assert "Conflicting expert feedback requires review." in updated.warnings
