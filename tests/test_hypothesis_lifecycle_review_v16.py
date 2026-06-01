from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from molecule_ranker.experiments.schemas import (
    ActiveLearningBatch,
    ActiveLearningSuggestion,
    AssayContext,
    AssayEndpoint,
    AssayResult,
)
from molecule_ranker.hypotheses import (
    HypothesisLifecycleManager,
    HypothesisReviewService,
    ResearchHypothesis,
)
from molecule_ranker.hypotheses.store import HypothesisStore
from molecule_ranker.portfolio.schemas import StageGate
from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace


def test_review_decision_updates_status_and_lifecycle(tmp_path: Path) -> None:
    store = _store_with(tmp_path, _hypothesis("hyp-review"))
    service = HypothesisReviewService(store)

    decision = service.record_decision(
        "hyp-review",
        reviewer_id="expert-1",
        decision="accept_for_planning",
        rationale="Human reviewer accepts for planning.",
        confidence=0.8,
        human_approval=True,
    )
    reloaded = store.get_hypothesis("hyp-review")
    events = store.list_lifecycle_events("hyp-review")

    assert decision.decision == "accept_for_planning"
    assert reloaded.status == "accepted_for_planning"
    assert decision.decision_id in reloaded.review_decision_ids
    assert reloaded.evidence_item_ids == []
    assert reloaded.assay_result_ids == []
    assert "review_decision_is_not_evidence" in decision.metadata
    assert [event.event_type for event in events][-2:] == ["updated", "accepted"]


def test_codex_cannot_approve_hypotheses(tmp_path: Path) -> None:
    store = _store_with(tmp_path, _hypothesis("hyp-codex"))
    service = HypothesisReviewService(store)

    with pytest.raises(ValueError, match="Codex cannot approve"):
        service.record_decision(
            "hyp-codex",
            reviewer_id="codex-assistant",
            decision="accept_for_planning",
            rationale="Automated approval is forbidden.",
            confidence=0.9,
        )

    assert store.get_hypothesis("hyp-codex").status == "proposed"
    assert store.list_review_decisions("hyp-codex") == []


def test_generated_molecule_requires_human_approval_when_configured(
    tmp_path: Path,
) -> None:
    store = _store_with(
        tmp_path,
        _hypothesis(
            "hyp-generated",
            hypothesis_type="generated_molecule",
            generated_molecule_entity_ids=["GEN-1"],
        ),
    )
    service = HypothesisReviewService(store)

    with pytest.raises(ValueError, match="explicit human approval"):
        service.record_decision(
            "hyp-generated",
            reviewer_id="expert-1",
            decision="accept_for_planning",
            rationale="Generated hypothesis needs explicit approval.",
            confidence=0.7,
        )

    service.record_decision(
        "hyp-generated",
        reviewer_id="expert-1",
        decision="accept_for_planning",
        rationale="Human approval recorded for generated hypothesis planning.",
        confidence=0.7,
        human_approval=True,
    )

    assert store.get_hypothesis("hyp-generated").status == "accepted_for_planning"


def test_review_workspace_can_include_hypotheses(tmp_path: Path) -> None:
    hypothesis = _hypothesis(
        "hyp-workspace",
        molecule_entity_ids=["CHEMBL887"],
        target_entity_ids=["MAOB"],
    )
    store = _store_with(tmp_path, hypothesis)
    service = HypothesisReviewService(store)

    workspace = service.attach_hypotheses_to_workspace(_workspace(), [hypothesis])

    assert workspace.metadata["hypothesis_ids"] == ["hyp-workspace"]
    assert workspace.metadata["hypotheses"][0]["not_evidence"] is True
    assert workspace.review_items[0].metadata["hypothesis_ids"] == ["hyp-workspace"]
    assert "planning remain separate" in workspace.review_items[0].metadata[
        "hypothesis_review_boundary"
    ]


def test_stage_gate_and_active_learning_batch_reference_hypotheses(
    tmp_path: Path,
) -> None:
    store = _store_with(tmp_path, _hypothesis("hyp-links"))
    lifecycle = HypothesisLifecycleManager(store)
    gate = StageGate(
        stage_gate_id="gate-1",
        name="Gate",
        from_stage="expert_review",
        to_stage="assay_candidate",
    )
    batch = ActiveLearningBatch(
        batch_id="batch-1",
        endpoint_name="target engagement",
        strategy="uncertainty",
        suggestions=[
            ActiveLearningSuggestion(
                suggestion_id="suggestion-1",
                candidate_name="Rasagiline",
                candidate_origin="existing",
                acquisition_score=0.6,
                acquisition_strategy="evidence_gap",
                rationale="High-level uncertainty reduction.",
                constraints_satisfied=True,
            )
        ],
    )

    linked_gate = lifecycle.link_stage_gate("hyp-links", gate, actor="portfolio-system")
    linked_batch = lifecycle.link_active_learning_batch(
        "hyp-links",
        batch,
        actor="active-learning-system",
    )
    reloaded = store.get_hypothesis("hyp-links")

    assert linked_gate.metadata["hypothesis_ids"] == ["hyp-links"]
    assert linked_batch.metadata["hypothesis_ids"] == ["hyp-links"]
    assert reloaded.metadata["stage_gate_ids"] == ["gate-1"]
    assert reloaded.metadata["active_learning_batch_ids"] == ["batch-1"]


def test_experimental_results_and_contradictions_update_status(
    tmp_path: Path,
) -> None:
    store = _store_with(tmp_path, _hypothesis("hyp-result"))
    lifecycle = HypothesisLifecycleManager(store)

    updated = lifecycle.update_from_experimental_result(
        "hyp-result",
        _assay_result("result-negative", outcome_label="negative"),
        actor="experimental-importer",
    )
    contradicted = lifecycle.mark_contradicted(
        "hyp-result",
        actor="kg-builder",
        contradicting_relation_ids=["rel:contradiction"],
        source_artifact_ids=["artifact:new"],
    )

    assert updated.status == "contradicted"
    assert "result-negative" in updated.assay_result_ids
    assert contradicted.status == "contradicted"
    assert "rel:contradiction" in contradicted.contradicting_relation_ids
    assert any(
        event.event_type == "contradicted"
        for event in store.list_lifecycle_events("hyp-result")
    )


def test_new_evidence_revives_stale_hypothesis(tmp_path: Path) -> None:
    store = _store_with(tmp_path, _hypothesis("hyp-stale", status="stale"))
    lifecycle = HypothesisLifecycleManager(store)

    revived = lifecycle.revive_with_evidence(
        "hyp-stale",
        actor="kg-builder",
        evidence_item_ids=["evidence:new"],
        supporting_relation_ids=["rel:new-support"],
        source_artifact_ids=["artifact:new"],
    )

    assert revived.status == "proposed"
    assert "evidence:new" in revived.evidence_item_ids
    assert "rel:new-support" in revived.supporting_relation_ids
    assert any(
        event.event_type == "revived"
        for event in store.list_lifecycle_events("hyp-stale")
    )


def _store_with(tmp_path: Path, hypothesis: ResearchHypothesis) -> HypothesisStore:
    store = HypothesisStore(tmp_path / "hypotheses.sqlite")
    store.create_hypothesis(hypothesis)
    return store


def _hypothesis(
    hypothesis_id: str,
    *,
    hypothesis_type: str = "molecule_target",
    status: str = "proposed",
    molecule_entity_ids: list[str] | None = None,
    generated_molecule_entity_ids: list[str] | None = None,
    target_entity_ids: list[str] | None = None,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id=hypothesis_id,
        hypothesis_type=hypothesis_type,  # type: ignore[arg-type]
        title="Graph-backed planning hypothesis",
        statement="A graph-backed hypothesis requires review before planning.",
        molecule_entity_ids=molecule_entity_ids or ["CHEMBL887"],
        generated_molecule_entity_ids=generated_molecule_entity_ids or [],
        target_entity_ids=target_entity_ids or ["MAOB"],
        supporting_relation_ids=["rel:support"],
        source_artifact_ids=["artifact:kg"],
        support_score=0.7,
        contradiction_score=0.1,
        novelty_score=0.3,
        testability_score=0.8,
        uncertainty_score=0.4,
        priority_score=0.6,
        confidence=0.65,
        status=status,  # type: ignore[arg-type]
    )


def _workspace() -> ReviewWorkspace:
    return ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        review_items=[
            ReviewItem(
                run_id="run-1",
                disease_name="Parkinson disease",
                candidate_id="CHEMBL887",
                candidate_name="Rasagiline",
                candidate_origin="existing",
                target_symbols=["MAOB"],
                priority_bucket="medium_priority",
                review_status="pending",
            )
        ],
    )


def _assay_result(result_id: str, *, outcome_label: str) -> AssayResult:
    endpoint = AssayEndpoint(
        endpoint_id="endpoint-target-engagement",
        name="target engagement",
        endpoint_category="target_engagement",
        directionality="binary",
    )
    return AssayResult(
        result_id=result_id,
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbol="MAOB",
        assay_context=AssayContext(
            assay_context_id="ctx-target-engagement",
            assay_name="target engagement assessment",
            assay_type="biochemical",
            target_symbol="MAOB",
            endpoint=endpoint,
        ),
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction="inactive",
        confidence=0.75,
        qc_status="passed",
        source="unit-test",
        imported_at=datetime.now(UTC),
    )
