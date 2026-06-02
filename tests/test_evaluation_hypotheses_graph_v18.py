from __future__ import annotations

import pytest

from molecule_ranker.evaluation.evaluators.graph import evaluate_graph
from molecule_ranker.evaluation.evaluators.hypotheses import evaluate_hypotheses


def _metric(report, name: str):
    for metric in report.metrics:
        if metric.name == name:
            return metric
    raise AssertionError(f"missing metric {name}")


def _hypothesis_artifact() -> dict[str, object]:
    return {
        "artifact_id": "hypotheses.json",
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "hypothesis_type": "mechanism",
                "review_decision": "accept",
                "has_contradiction": False,
                "contradiction_resolved": False,
                "retired": False,
                "is_stale": False,
                "stale_detected": False,
                "evidence_gap_open": True,
                "evidence_gap_closed": True,
            },
            {
                "hypothesis_id": "H2",
                "hypothesis_type": "generated_molecule",
                "generated_id": "G2",
                "canonical_smiles": "CCN",
                "review_decision": "accept",
                "has_contradiction": True,
                "contradiction_resolved": True,
                "retired": True,
                "is_stale": True,
                "stale_detected": True,
                "evidence_gap_open": True,
                "evidence_gap_closed": False,
            },
            {
                "hypothesis_id": "H3",
                "hypothesis_type": "generated_molecule",
                "generated_id": "G3",
                "canonical_smiles": "CCO",
                "review_decision": "reject",
                "has_contradiction": True,
                "contradiction_resolved": False,
                "retired": False,
                "is_stale": True,
                "stale_detected": False,
                "evidence_gap_open": True,
                "evidence_gap_closed": True,
            },
        ],
    }


def _hypothesis_evidence() -> dict[str, object]:
    return {
        "artifact_id": "imported_evidence.json",
        "evidence": [
            {
                "hypothesis_id": "H1",
                "support_status": "supported",
                "source_type": "imported_evidence",
            },
            {
                "hypothesis_id": "H2",
                "generated_id": "G2",
                "canonical_smiles": "CCN",
                "support_status": "supported",
                "source_type": "imported_evidence",
            },
            {
                "hypothesis_id": "H3",
                "generated_id": "G3",
                "canonical_smiles": "CCC",
                "support_status": "contradicted",
                "source_type": "imported_evidence",
            },
        ],
    }


def test_hypothesis_evaluator_scores_lifecycle_against_imported_evidence() -> None:
    report = evaluate_hypotheses(
        hypothesis_artifacts={"hypotheses": _hypothesis_artifact()},
        imported_evidence_context={"imported_evidence": _hypothesis_evidence()},
        evaluation_id="hypothesis-eval",
    )

    assert _metric(report, "support_rate_after_outcomes").value == pytest.approx(2 / 3)
    assert _metric(report, "contradiction_resolution_rate").value == pytest.approx(0.5)
    assert _metric(report, "retirement_rate").value == pytest.approx(1 / 3)
    assert _metric(report, "stale_hypothesis_detection_rate").value == pytest.approx(0.5)
    assert _metric(report, "evidence_gap_closure_rate").value == pytest.approx(2 / 3)
    assert _metric(report, "generated_hypothesis_exact_result_rate").value == pytest.approx(0.5)
    assert _metric(report, "review_acceptance_vs_outcome_alignment").value == pytest.approx(1.0)
    assert report.metadata["rules"]["hypothesis_support_scoped_to_imported_evidence"] is True
    assert "mechanism_hypotheses_are_not_causality_proof" in report.limitations


def test_hypothesis_support_requires_imported_evidence_context() -> None:
    report = evaluate_hypotheses(
        hypothesis_artifacts={"hypotheses": _hypothesis_artifact()},
        imported_evidence_context={},
    )

    assert _metric(report, "support_rate_after_outcomes").value is None
    assert "no_imported_evidence_context" in report.warnings


def _graph_artifact() -> dict[str, object]:
    return {
        "artifact_id": "knowledge_graph.json",
        "entities": [
            {"entity_id": "E1", "canonical_id": "MOL1", "provenance_ids": ["prov-1"]},
            {
                "entity_id": "E2",
                "canonical_id": "MOL1",
                "dedup_conflict": True,
                "provenance_ids": [],
            },
            {"entity_id": "E3", "canonical_id": "TARGET1", "provenance_ids": ["prov-2"]},
        ],
        "relations": [
            {
                "relation_id": "R1",
                "predicate": "supports",
                "inferred": False,
                "provenance_ids": ["prov-1"],
                "grounded": True,
            },
            {
                "relation_id": "R2",
                "predicate": "inferred_supports",
                "inferred": True,
                "provenance_ids": ["prov-2"],
                "grounded": True,
            },
            {
                "relation_id": "R3",
                "predicate": "contradicts",
                "inferred": False,
                "provenance_ids": [],
                "contradiction_detected": True,
                "grounded": False,
            },
        ],
        "contradiction_cases": [
            {"case_id": "C1", "detected": True},
            {"case_id": "C2", "detected": False},
        ],
        "stale_decision_cases": [
            {"case_id": "S1", "detected": True},
            {"case_id": "S2", "detected": False},
        ],
        "graph_queries": [
            {"query_id": "Q1", "grounded": True},
            {"query_id": "Q2", "grounded": False},
        ],
    }


def test_graph_evaluator_scores_graph_quality_and_grounding() -> None:
    report = evaluate_graph(
        graph_artifacts={"knowledge_graph": _graph_artifact()},
        evaluation_id="graph-eval",
    )

    assert _metric(report, "entity_deduplication_conflict_rate").value == pytest.approx(1 / 3)
    assert _metric(report, "provenance_coverage").value == pytest.approx(4 / 6)
    assert _metric(report, "inferred_relation_fraction").value == pytest.approx(1 / 3)
    assert _metric(report, "contradiction_detection_coverage").value == pytest.approx(0.5)
    assert _metric(report, "stale_decision_detection_coverage").value == pytest.approx(0.5)
    assert _metric(report, "graph_query_grounding_rate").value == pytest.approx(0.5)
    assert report.metadata["rules"]["graph_outputs_are_evaluation_artifacts"] is True
