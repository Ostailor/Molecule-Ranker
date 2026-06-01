from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from molecule_ranker.knowledge_graph.reasoning import (
    GRAPH_QUERY_WARNING,
    GraphReasoner,
    candidates_for_target,
    candidates_with_contradictory_evidence,
    evidence_gaps_for_candidate,
    generated_molecules_without_direct_evidence,
    graph_paths_between_disease_and_molecule,
    mechanisms_for_disease,
    mechanisms_supported_across_programs,
    molecules_with_safety_concerns_across_programs,
    portfolios_reusing_same_scaffold_risk,
    projects_with_stale_model_predictions,
    scaffolds_with_positive_assay_history,
    targets_with_repeated_developability_failures,
)
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph


@pytest.fixture
def query_graph() -> KnowledgeGraph:
    now = datetime.now(UTC)
    entities = [
        _entity("disease:pd", "disease", "Parkinson disease"),
        _entity("target:MAOB", "target", "MAOB", identifiers={"HGNC": "6834"}),
        _entity("target:LRRK2", "target", "LRRK2"),
        _entity("mechanism:maob", "mechanism", "MAOB inhibition"),
        _entity("pathway:dopamine", "pathway", "Dopamine metabolism"),
        _entity("molecule:rasagiline", "molecule", "Rasagiline"),
        _entity("molecule:safinamide", "molecule", "Safinamide"),
        _entity("molecule:risky-a", "molecule", "Risky A"),
        _entity("molecule:risky-b", "molecule", "Risky B"),
        _entity("generated_molecule:gen1", "generated_molecule", "Generated MAOB 1"),
        _entity("scaffold:propargylamine", "scaffold", "Propargylamine"),
        _entity("developability_alert:herg", "developability_alert", "hERG alert"),
        _entity("assay_result:pos", "assay_result", "Positive assay"),
        _entity("assay_result:neg", "assay_result", "Negative assay"),
        _entity("portfolio:p1", "portfolio", "Portfolio 1"),
        _entity("portfolio:p2", "portfolio", "Portfolio 2"),
        _entity("project:pd", "project", "PD project"),
        _entity(
            "model_prediction:old",
            "model_prediction",
            "Old MAOB model",
            metadata={"project_id": "project:pd"},
        ),
    ]
    relations = [
        _rel(
            "dt-maob",
            "disease:pd",
            "associated_with",
            "target:MAOB",
            0.82,
            "evidence_backed",
            ["program:a"],
        ),
        _rel(
            "target-mech-a",
            "target:MAOB",
            "has_mechanism",
            "mechanism:maob",
            0.8,
            "evidence_backed",
            ["program:a"],
        ),
        _rel(
            "target-mech-b",
            "molecule:safinamide",
            "has_mechanism",
            "mechanism:maob",
            0.74,
            "evidence_backed",
            ["program:b"],
        ),
        _rel(
            "target-pathway",
            "target:MAOB",
            "associated_with",
            "pathway:dopamine",
            0.7,
            "evidence_backed",
            ["program:a"],
        ),
        _rel(
            "ras-target",
            "molecule:rasagiline",
            "targets",
            "target:MAOB",
            0.9,
            "evidence_backed",
            ["program:a"],
            metadata={"candidate_score": 0.9},
        ),
        _rel(
            "saf-target",
            "molecule:safinamide",
            "targets",
            "target:MAOB",
            0.76,
            "evidence_backed",
            ["program:b"],
            metadata={"candidate_score": 0.76},
        ),
        _rel(
            "risk-a-target",
            "molecule:risky-a",
            "targets",
            "target:MAOB",
            0.5,
            "evidence_backed",
            ["program:a"],
            metadata={"candidate_score": 0.35},
        ),
        _rel(
            "risk-b-target",
            "molecule:risky-b",
            "targets",
            "target:MAOB",
            0.5,
            "evidence_backed",
            ["program:b"],
            metadata={"candidate_score": 0.3},
        ),
        _rel(
            "gen-target",
            "generated_molecule:gen1",
            "hypothesizes",
            "target:MAOB",
            0.66,
            "inferred",
            ["graph:gen"],
        ),
        _rel(
            "gen-no-evidence",
            "generated_molecule:gen1",
            "has_no_direct_evidence",
            "generated_molecule:gen1",
            1.0,
            "inferred",
            ["graph:gen"],
        ),
        _rel(
            "ras-scaffold",
            "molecule:rasagiline",
            "has_scaffold",
            "scaffold:propargylamine",
            0.9,
            "computational",
            ["program:a"],
        ),
        _rel(
            "risk-a-scaffold",
            "molecule:risky-a",
            "has_scaffold",
            "scaffold:propargylamine",
            0.8,
            "computational",
            ["program:a"],
        ),
        _rel(
            "risk-b-scaffold",
            "molecule:risky-b",
            "has_scaffold",
            "scaffold:propargylamine",
            0.8,
            "computational",
            ["program:b"],
        ),
        _rel(
            "assay-pos",
            "assay_result:pos",
            "supports",
            "molecule:rasagiline",
            0.88,
            "experimental",
            ["assay:pos"],
            metadata={"qc_status": "passed", "outcome_label": "positive", "target_symbol": "MAOB"},
        ),
        _rel(
            "assay-neg",
            "assay_result:neg",
            "contradicts",
            "molecule:risky-a",
            0.86,
            "experimental",
            ["assay:neg"],
            metadata={"qc_status": "passed", "outcome_label": "negative", "target_symbol": "MAOB"},
        ),
        _rel(
            "risk-a",
            "molecule:risky-a",
            "has_developability_risk",
            "developability_alert:herg",
            0.86,
            "computational",
            ["program:a"],
        ),
        _rel(
            "risk-b",
            "molecule:risky-b",
            "has_developability_risk",
            "developability_alert:herg",
            0.83,
            "computational",
            ["program:b"],
        ),
        _rel(
            "gen-risk",
            "generated_molecule:gen1",
            "has_developability_risk",
            "developability_alert:herg",
            0.9,
            "computational",
            ["program:a"],
        ),
        _rel(
            "select-a",
            "molecule:risky-a",
            "selected_in_portfolio",
            "portfolio:p1",
            0.7,
            "computational",
            ["portfolio:p1"],
        ),
        _rel(
            "select-b",
            "molecule:risky-b",
            "selected_in_portfolio",
            "portfolio:p2",
            0.7,
            "computational",
            ["portfolio:p2"],
        ),
        _rel(
            "model-old",
            "model_prediction:old",
            "predicted_by_model",
            "generated_molecule:gen1",
            0.79,
            "model_prediction",
            ["project:pd"],
            metadata={"score": 0.79, "project_id": "project:pd"},
            created_at=now - timedelta(days=90),
        ),
        _rel(
            "stale-model",
            "model_prediction:old",
            "stale_due_to",
            "generated_molecule:gen1",
            0.8,
            "inferred",
            ["graph:stale"],
            metadata={
                "reason": "model_trained_before_newer_assay_result",
                "project_id": "project:pd",
            },
        ),
    ]
    return KnowledgeGraph(graph_id="kg-query-test", entities=entities, relations=relations)


def test_candidates_for_target(query_graph: KnowledgeGraph) -> None:
    results = candidates_for_target(query_graph, "MAOB")
    assert any(_has_entity(result, "molecule:rasagiline") for result in results)
    _assert_result_contract(results[0])


def test_mechanisms_for_disease(query_graph: KnowledgeGraph) -> None:
    results = mechanisms_for_disease(query_graph, "Parkinson disease")
    assert any(_has_entity(result, "mechanism:maob") for result in results)
    assert any(_has_entity(result, "pathway:dopamine") for result in results)
    _assert_result_contract(results[0])


def test_generated_molecules_without_direct_evidence(query_graph: KnowledgeGraph) -> None:
    results = generated_molecules_without_direct_evidence(query_graph)
    assert [result.entity_refs[0].entity_id for result in results] == ["generated_molecule:gen1"]
    assert "Generated molecules without direct evidence" in " ".join(results[0].warnings)


def test_candidates_with_contradictory_evidence(query_graph: KnowledgeGraph) -> None:
    results = candidates_with_contradictory_evidence(query_graph)
    assert any(_has_entity(result, "molecule:risky-a") for result in results)
    _assert_result_contract(results[0])


def test_scaffolds_with_positive_assay_history(query_graph: KnowledgeGraph) -> None:
    results = scaffolds_with_positive_assay_history(query_graph)
    assert any(_has_entity(result, "scaffold:propargylamine") for result in results)
    assert any(
        "assay-pos" in {ref.relation_id for ref in result.relation_refs} for result in results
    )


def test_targets_with_repeated_developability_failures(query_graph: KnowledgeGraph) -> None:
    results = targets_with_repeated_developability_failures(query_graph)
    assert [result.entity_refs[0].entity_id for result in results] == ["target:MAOB"]
    assert results[0].metadata["failed_candidate_count"] >= 2


def test_mechanisms_supported_across_programs(query_graph: KnowledgeGraph) -> None:
    results = mechanisms_supported_across_programs(query_graph)
    assert [result.entity_refs[0].entity_id for result in results] == ["mechanism:maob"]
    assert set(results[0].metadata["program_ids"]) >= {"program:a", "program:b"}


def test_molecules_with_safety_concerns_across_programs(query_graph: KnowledgeGraph) -> None:
    results = molecules_with_safety_concerns_across_programs(query_graph)
    assert any(_has_entity(result, "developability_alert:herg") for result in results)
    assert any(_has_entity(result, "molecule:risky-a") for result in results)


def test_portfolios_reusing_same_scaffold_risk(query_graph: KnowledgeGraph) -> None:
    results = portfolios_reusing_same_scaffold_risk(query_graph)
    assert len(results) == 1
    assert set(results[0].metadata["portfolio_ids"]) == {"portfolio:p1", "portfolio:p2"}


def test_projects_with_stale_model_predictions(query_graph: KnowledgeGraph) -> None:
    results = projects_with_stale_model_predictions(query_graph)
    assert len(results) == 1
    assert _has_entity(results[0], "project:pd")
    assert results[0].metadata["project_id"] == "project:pd"


def test_graph_paths_between_disease_and_molecule(query_graph: KnowledgeGraph) -> None:
    results = graph_paths_between_disease_and_molecule(
        query_graph,
        "Parkinson disease",
        "molecule:rasagiline",
    )
    assert results
    assert results[0].path_entity_ids[0] == "disease:pd"
    assert results[0].path_entity_ids[-1] == "molecule:rasagiline"
    assert results[0].provenance


def test_evidence_gaps_for_candidate(query_graph: KnowledgeGraph) -> None:
    results = evidence_gaps_for_candidate(query_graph, "generated_molecule:gen1")
    assert len(results) == 1
    assert results[0].metadata["gap_count"] >= 2
    assert "direct experimental evidence" in " ".join(results[0].warnings)


def test_graph_reasoner_method_api(query_graph: KnowledgeGraph) -> None:
    reasoner = GraphReasoner(query_graph)
    assert reasoner.candidates_for_target("MAOB")


def _entity(
    entity_id: str,
    entity_type: str,
    name: str,
    *,
    identifiers: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> GraphEntity:
    return GraphEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        identifiers=identifiers or {},
        metadata=metadata or {},
    )


def _rel(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    confidence: float,
    relation_type: str,
    provenance: list[str],
    *,
    metadata: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> GraphRelation:
    timestamp = created_at or datetime.now(UTC)
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=confidence,
        direction="contradictory" if predicate == "contradicts" else "supportive",
        source_artifact_ids=provenance,
        source_record_ids=[relation_id],
        created_at=timestamp,
        updated_at=timestamp,
        metadata=metadata or {},
    )


def _has_entity(result: object, entity_id: str) -> bool:
    return any(ref.entity_id == entity_id for ref in result.entity_refs)  # type: ignore[attr-defined]


def _assert_result_contract(result: object) -> None:
    assert result.entity_refs  # type: ignore[attr-defined]
    assert result.relation_refs  # type: ignore[attr-defined]
    assert result.provenance  # type: ignore[attr-defined]
    assert GRAPH_QUERY_WARNING in result.warnings  # type: ignore[attr-defined]
    assert 0.0 <= result.confidence <= 1.0  # type: ignore[attr-defined]
