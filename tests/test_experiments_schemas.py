from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.experiments.schemas import (
    ActiveLearningBatch,
    ActiveLearningSuggestion,
    AssayContext,
    AssayEndpoint,
    AssayResult,
    ExperimentalEvidenceSummary,
    ExperimentalLearningDataset,
    ExperimentAuditEvent,
)


def _endpoint() -> AssayEndpoint:
    return AssayEndpoint(
        endpoint_id="endpoint-binding-affinity",
        name="binding_affinity",
        endpoint_category="potency",
        unit="nM",
        directionality="lower_is_better",
        description="High-level binding affinity endpoint.",
    )


def _context() -> AssayContext:
    return AssayContext(
        assay_context_id="assay-context-1",
        assay_name="Binding screen",
        assay_type="biochemical",
        target_symbol="MAOB",
        target_identifiers={"uniprot": "P27338"},
        disease_name="Parkinson disease",
        model_system="recombinant_protein",
        species="human",
        endpoint=_endpoint(),
        protocol_reference="External SOP assay-42",
        protocol_summary="High-level biochemical binding assay summary.",
    )


def test_assay_endpoint_allows_declared_categories_and_score_bounds():
    endpoint = _endpoint()

    assert endpoint.endpoint_category == "potency"
    assert endpoint.directionality == "lower_is_better"
    assert endpoint.metadata == {}

    with pytest.raises(ValidationError):
        AssayEndpoint.model_validate(
            {
                "endpoint_id": "bad",
                "name": "binding_affinity",
                "endpoint_category": "clinical",
                "directionality": "lower_is_better",
            }
        )


def test_assay_context_rejects_protocol_steps_and_procedural_details():
    context = _context()

    assert context.protocol_reference == "External SOP assay-42"
    assert context.protocol_summary == "High-level biochemical binding assay summary."

    with pytest.raises(ValidationError, match="must not include"):
        AssayContext(
            **{
                **context.model_dump(),
                "protocol_summary": "Step 1 incubate with reagent at 37 C for 30 minutes.",
            }
        )


def test_assay_result_validates_outcomes_confidence_timezone_and_defaults():
    result = AssayResult(
        result_id="result-1",
        run_id="run-1",
        workspace_id="workspace-1",
        review_item_id="review-item-1",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        inchi_key="RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        assay_context=_context(),
        measured_value=12.5,
        measured_value_numeric=12.5,
        unit="nM",
        relation="<=",
        normalized_value=12.5,
        normalized_unit="nM",
        outcome_label="positive",
        activity_direction="active",
        replicate_count=2,
        replicate_values=[11.9, 13.1],
        uncertainty=0.4,
        confidence=0.82,
        qc_status="passed",
        result_date=date(2026, 1, 2),
        source="csv_import",
        source_record_id="row-1",
        imported_at=datetime(2026, 1, 3, 4, 5, tzinfo=UTC),
        imported_by="analyst-1",
        notes="Imported user assay result.",
    )

    assert result.confidence == 0.82
    assert result.replicate_values == [11.9, 13.1]
    assert result.metadata == {}

    with pytest.raises(ValidationError):
        AssayResult(**{**result.model_dump(), "confidence": 1.2})
    with pytest.raises(ValidationError):
        AssayResult(**{**result.model_dump(), "outcome_label": "cured"})
    with pytest.raises(ValidationError):
        AssayResult(
            **{
                **result.model_dump(),
                "imported_at": datetime(2026, 1, 3, 4, 5),
            }
        )


def test_evidence_summary_and_learning_dataset_validate_scores_and_timestamps():
    summary = ExperimentalEvidenceSummary(
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        result_count=2,
        positive_count=1,
        negative_count=1,
        inconclusive_count=0,
        failed_qc_count=0,
        endpoint_summaries={"binding_affinity": {"positive": 1, "negative": 1}},
        best_supporting_results=["result-1"],
        key_negative_results=["result-2"],
        safety_concerns=[],
        confidence=0.7,
        interpretation="Mixed imported assay evidence; no clinical claim.",
        warnings=[],
    )
    dataset = ExperimentalLearningDataset(
        dataset_id="dataset-1",
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        endpoint_name="binding_affinity",
        rows=[{"candidate_id": "CHEMBL887", "label": 1}],
        feature_schema={"canonical_smiles": "string"},
        label_schema={"outcome_label": ["positive", "negative"]},
        included_result_ids=["result-1"],
        excluded_result_ids=["result-2"],
        exclusion_reasons={"result-2": "failed_qc"},
    )

    assert summary.confidence == 0.7
    assert dataset.created_at.tzinfo is not None

    with pytest.raises(ValidationError):
        ExperimentalEvidenceSummary(**{**summary.model_dump(), "confidence": -0.1})
    with pytest.raises(ValidationError):
        ExperimentalLearningDataset(**{**dataset.model_dump(), "created_at": datetime(2026, 1, 4)})


def test_active_learning_and_audit_event_schemas_validate_bounds_and_timezone():
    suggestion = ActiveLearningSuggestion(
        suggestion_id="suggestion-1",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbol="MAOB",
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        acquisition_score=0.74,
        acquisition_strategy="evidence_gap",
        rationale="Unresolved imported assay evidence gap.",
        uncertainty_score=0.6,
        diversity_score=0.4,
        expected_value_score=0.7,
        risk_penalty=0.1,
        constraints_satisfied=True,
        warnings=[],
    )
    batch = ActiveLearningBatch(
        batch_id="batch-1",
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        endpoint_name="binding_affinity",
        strategy="balanced",
        suggestions=[suggestion],
        excluded_candidates=[{"candidate_id": "CHEMBL1", "reason": "failed_qc"}],
    )
    audit = ExperimentAuditEvent(
        event_id="audit-1",
        event_type="assay_result_imported",
        actor="analyst-1",
        timestamp=datetime(2026, 1, 6, tzinfo=UTC),
        object_type="AssayResult",
        object_id="result-1",
        summary="Imported assay result from user file.",
    )

    assert batch.suggestions[0].acquisition_score == 0.74
    assert audit.timestamp.tzinfo is not None

    with pytest.raises(ValidationError):
        ActiveLearningSuggestion(**{**suggestion.model_dump(), "acquisition_score": 1.01})
    with pytest.raises(ValidationError):
        ActiveLearningBatch(**{**batch.model_dump(), "created_at": datetime(2026, 1, 5)})
