from __future__ import annotations

from datetime import UTC, date, datetime

from molecule_ranker.experiments.evidence import assay_result_to_evidence_item
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult


def _result(
    *,
    outcome_label: str = "positive",
    activity_direction: str = "active",
    qc_status: str = "passed",
    confidence: float = 0.8,
) -> AssayResult:
    return AssayResult(
        result_id="result-1",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbol="MAOB",
        assay_context=AssayContext(
            assay_context_id="context-1",
            assay_name="Binding screen",
            assay_type="biochemical",
            target_symbol="MAOB",
            endpoint=AssayEndpoint(
                endpoint_id="endpoint-1",
                name="binding_affinity",
                endpoint_category="potency",
                unit="nM",
                directionality="lower_is_better",
            ),
        ),
        measured_value=12.5,
        measured_value_numeric=12.5,
        unit="nM",
        normalized_value=12.5,
        normalized_unit="nM",
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        replicate_count=2,
        replicate_values=[11.9, 13.1],
        confidence=confidence,
        qc_status=qc_status,  # type: ignore[arg-type]
        result_date=date(2026, 1, 2),
        source="csv_import",
        source_record_id="row-1",
        imported_at=datetime(2026, 1, 3, tzinfo=UTC),
        metadata={"link_method": "candidate_id", "link_confidence": 1.0},
    )


def test_assay_result_to_positive_evidence_item_preserves_provenance():
    evidence = assay_result_to_evidence_item(_result())

    assert evidence.source == "Imported experimental result"
    assert evidence.source_record_id == "result-1"
    assert evidence.title == "Binding screen result for Rasagiline"
    assert evidence.evidence_type == "experimental_positive"
    assert evidence.confidence == 0.8
    assert evidence.metadata["result_id"] == "result-1"
    assert evidence.metadata["source_record_id"] == "row-1"
    assert evidence.metadata["endpoint_category"] == "potency"
    assert evidence.metadata["link_method"] == "candidate_id"
    assert evidence.metadata["link_confidence"] == 1.0


def test_assay_result_to_safety_concern_evidence_is_not_safe_or_effective_claim():
    evidence = assay_result_to_evidence_item(
        _result(outcome_label="negative", activity_direction="toxic")
    )

    assert evidence.evidence_type == "experimental_safety_concern"
    assert "safe" not in evidence.summary.lower()
    assert "effective" not in evidence.summary.lower()
    assert "clinical" not in evidence.summary.lower()
    assert "treatment" not in evidence.summary.lower()


def test_assay_result_to_inconclusive_and_failed_qc_types():
    inconclusive = assay_result_to_evidence_item(
        _result(outcome_label="inconclusive", activity_direction="ambiguous")
    )
    failed = assay_result_to_evidence_item(
        _result(outcome_label="failed_qc", activity_direction="ambiguous", qc_status="failed")
    )

    assert inconclusive.evidence_type == "experimental_inconclusive"
    assert failed.evidence_type == "experimental_failed_qc"
    assert failed.confidence == 0.0


def test_assay_result_evidence_metadata_excludes_protocol_fields():
    evidence = assay_result_to_evidence_item(_result())

    assert "protocol_reference" not in evidence.metadata
    assert "protocol_summary" not in evidence.metadata
    assert "reagents" not in str(evidence.metadata).lower()
