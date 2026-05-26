from __future__ import annotations

import json
from datetime import UTC, datetime

from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.review.dossier import DossierWriterAgent, render_dossier_markdown
from molecule_ranker.review.experimental_results import (
    apply_experimental_results_to_review_workspace,
)
from molecule_ranker.review.schemas import (
    FollowupRequest,
    Reviewer,
    ReviewerDecision,
    ReviewItem,
    ReviewWorkspace,
)
from molecule_ranker.review.validation_handoff import build_validation_handoff


def _endpoint(
    *,
    category: str = "potency",
    name: str = "binding_affinity",
) -> AssayEndpoint:
    return AssayEndpoint(
        endpoint_id=f"endpoint-{name}",
        name=name,
        endpoint_category=category,  # type: ignore[arg-type]
        unit="nM",
        directionality="lower_is_better",
    )


def _result(
    result_id: str,
    *,
    outcome_label: str = "positive",
    activity_direction: str = "active",
    qc_status: str = "passed",
    endpoint: AssayEndpoint | None = None,
) -> AssayResult:
    endpoint = endpoint or _endpoint()
    return AssayResult(
        result_id=result_id,
        review_item_id="review-item-run-1-chembl887",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        assay_context=AssayContext(
            assay_context_id="ctx-1",
            assay_name=f"{endpoint.name} assay",
            assay_type="safety" if endpoint.endpoint_category == "safety" else "biochemical",
            target_symbol="MAOB",
            disease_name="Parkinson disease",
            endpoint=endpoint,
        ),
        measured_value=12.0,
        measured_value_numeric=12.0,
        unit="nM",
        normalized_value=12.0,
        normalized_unit="nM",
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        confidence=0.82,
        qc_status=qc_status,  # type: ignore[arg-type]
        source="csv_import",
        source_record_id=f"src-{result_id}",
        imported_at=datetime.now(UTC),
    )


def _workspace() -> ReviewWorkspace:
    item = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        score=0.72,
        confidence=0.68,
        evidence_summary={
            "target_evidence_count": 3,
            "molecule_evidence_count": 2,
            "literature_claim_counts": {"supports": 1, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 0,
            "developability_risk_level": "low",
        },
        literature_summary={"items": []},
        developability_summary={"risk_level": "low"},
        priority_bucket="medium_priority",
        review_status="pending",
    )
    return ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        review_items=[item],
        created_at=datetime.now(UTC),
    )


def test_review_item_includes_experimental_result_summary():
    workspace = apply_experimental_results_to_review_workspace(
        _workspace(),
        [_result("result-1")],
    )

    item = workspace.review_items[0]
    summary = item.evidence_summary["experimental_results"]
    assert summary["result_count"] == 1
    assert summary["positive_count"] == 1
    assert summary["results"][0]["result_id"] == "result-1"
    assert summary["results"][0]["source_record_id"] == "src-result-1"
    assert item.metadata["experimental_evidence_boundary"].startswith(
        "Imported experimental results remain separate"
    )


def test_positive_result_suggests_review_without_auto_accepting():
    workspace = apply_experimental_results_to_review_workspace(
        _workspace(),
        [_result("positive")],
    )
    item = workspace.review_items[0]

    assert item.review_status == "needs_expert_review"
    assert item.metadata["experimental_review_suggestion"]["suggested_decision"] == (
        "accept_for_followup"
    )
    assert workspace.decisions == []

    auto_workspace = apply_experimental_results_to_review_workspace(
        _workspace(),
        [_result("positive")],
        config={"allow_experimental_auto_accept": True},
    )
    assert auto_workspace.review_items[0].review_status == "accepted"


def test_safety_result_escalates_and_suggests_safety_followup():
    safety_endpoint = _endpoint(category="safety", name="cytotoxicity")
    workspace = apply_experimental_results_to_review_workspace(
        _workspace(),
        [
            _result(
                "safety",
                outcome_label="negative",
                activity_direction="toxic",
                endpoint=safety_endpoint,
            )
        ],
    )

    item = workspace.review_items[0]
    assert item.review_status == "escalated"
    assert item.metadata["experimental_review_suggestion"]["suggested_decision"] == (
        "escalate_to_expert"
    )
    assert "experimental_safety_concern" in item.risk_flags


def test_dossier_and_handoff_include_result_summary_separate_from_decisions():
    workspace = _workspace()
    reviewer = Reviewer(reviewer_id="expert-1", role="medicinal_chemist")
    workspace.decisions.append(
        ReviewerDecision(
            review_item_id=workspace.review_items[0].review_item_id,
            reviewer=reviewer,
            decision="needs_more_data",
            rationale="Expert decision remains separate from imported results.",
            confidence=0.6,
        )
    )
    workspace = apply_experimental_results_to_review_workspace(workspace, [_result("result-1")])

    dossier = DossierWriterAgent().build_dossier(
        workspace,
        workspace.review_items[0].review_item_id,
    )
    markdown = render_dossier_markdown(dossier)
    handoff = build_validation_handoff(workspace, workspace.review_items[0].review_item_id)
    serialized = json.dumps(handoff.model_dump(mode="json"))

    assert "Experimental evidence" in markdown
    assert "result-1" in markdown
    assert "Reviewer decisions and comments" in markdown
    assert "needs_more_data" in markdown
    assert handoff.metadata["experimental_result_summary"]["result_count"] == 1
    assert "result-1" in serialized
    assert workspace.decisions[0].decision == "needs_more_data"


def test_new_followup_request_types_are_allowed():
    reviewer = Reviewer(reviewer_id="expert-1")
    for request_type in [
        "repeat_assay_review",
        "orthogonal_validation_review",
        "safety_followup_review",
        "result_qc_review",
        "active_learning_batch_review",
    ]:
        request = FollowupRequest(
            review_item_id="review-1",
            requested_by=reviewer,
            request_type=request_type,  # type: ignore[arg-type]
            request_text="Review imported result summary at a high level.",
            priority="medium",
            status="open",
        )
        assert request.request_type == request_type
