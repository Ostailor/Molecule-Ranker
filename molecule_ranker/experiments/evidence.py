from __future__ import annotations

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.experiments.validation import result_quality_score
from molecule_ranker.schemas import EvidenceItem


def assay_result_to_evidence_item(result: AssayResult) -> EvidenceItem:
    endpoint = result.assay_context.endpoint
    return EvidenceItem(
        source="Imported experimental result",
        source_record_id=result.result_id or result.source_record_id,
        title=f"{result.assay_context.assay_name} result for {result.candidate_name}",
        evidence_type=_evidence_type(result),
        summary=(
            f"Imported assay result reports {result.outcome_label} for "
            f"{result.candidate_name} in {result.assay_context.assay_name} "
            f"endpoint {endpoint.name}."
        ),
        confidence=_confidence(result),
        metadata={
            "result_id": result.result_id,
            "assay_name": result.assay_context.assay_name,
            "assay_type": result.assay_context.assay_type,
            "endpoint_name": endpoint.name,
            "endpoint_category": endpoint.endpoint_category,
            "measured_value": result.measured_value,
            "unit": result.unit,
            "normalized_value": result.normalized_value,
            "normalized_unit": result.normalized_unit,
            "outcome_label": result.outcome_label,
            "activity_direction": result.activity_direction,
            "qc_status": result.qc_status,
            "replicate_count": result.replicate_count,
            "result_date": result.result_date.isoformat() if result.result_date else None,
            "source_record_id": result.source_record_id,
            "link_method": result.metadata.get("link_method"),
            "link_confidence": result.metadata.get("link_confidence"),
        },
    )


def _evidence_type(result: AssayResult) -> str:
    if result.outcome_label == "failed_qc" or result.qc_status == "failed":
        return "experimental_failed_qc"
    if result.activity_direction in {"toxic", "worsened"}:
        return "experimental_safety_concern"
    if result.outcome_label == "positive":
        return "experimental_positive"
    if result.outcome_label == "negative":
        return "experimental_negative"
    return "experimental_inconclusive"


def _confidence(result: AssayResult) -> float:
    return result_quality_score(result)
