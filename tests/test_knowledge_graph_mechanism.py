from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.knowledge_graph.mechanism import extract_mechanism_hypotheses
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation, KnowledgeGraph


def test_extract_supported_mechanism_hypothesis() -> None:
    graph = _graph(
        [
            _entity("disease:pd", "disease", "Parkinson disease"),
            _entity("target:MAOB", "target", "MAOB"),
            _entity("molecule:rasagiline", "molecule", "Rasagiline"),
            _entity("mechanism:maob-inhibition", "mechanism", "MAOB inhibition"),
            _entity("assay_result:positive", "assay_result", "Positive MAOB assay"),
            _entity("literature_claim:maob", "literature_claim", "MAOB mechanism claim"),
        ],
        [
            _rel("dt", "disease:pd", "associated_with", "target:MAOB", 0.82),
            _rel("mt", "molecule:rasagiline", "targets", "target:MAOB", 0.86),
            _rel("tm", "target:MAOB", "has_mechanism", "mechanism:maob-inhibition", 0.8),
            _rel(
                "lit",
                "literature_claim:maob",
                "supports",
                "mechanism:maob-inhibition",
                0.74,
                relation_type="literature",
            ),
            _rel(
                "assay",
                "assay_result:positive",
                "supports",
                "molecule:rasagiline",
                0.9,
                relation_type="experimental",
                metadata={"qc_status": "passed", "outcome_label": "positive"},
            ),
        ],
    )

    hypotheses = extract_mechanism_hypotheses(graph)

    assert len(hypotheses) == 1
    hypothesis = hypotheses[0]
    assert hypothesis.status == "supported"
    assert hypothesis.support_score >= 0.7
    assert hypothesis.contradiction_score == 0.0
    assert hypothesis.disease_entity_id == "disease:pd"
    assert hypothesis.target_entity_ids == ["target:MAOB"]
    assert hypothesis.molecule_entity_ids == ["molecule:rasagiline"]
    assert hypothesis.claim_entity_ids == ["literature_claim:maob"]
    assert "causality" in " ".join(hypothesis.warnings)


def test_extract_contradicted_mechanism_surfaces_contradictions() -> None:
    graph = _graph(
        [
            _entity("disease:pd", "disease", "Parkinson disease"),
            _entity("target:MAOB", "target", "MAOB"),
            _entity("molecule:gen-risk", "molecule", "Risky candidate"),
            _entity("mechanism:maob-inhibition", "mechanism", "MAOB inhibition"),
            _entity("assay_result:negative", "assay_result", "Negative MAOB assay"),
            _entity("developability_alert:herg", "developability_alert", "hERG alert"),
        ],
        [
            _rel("dt", "disease:pd", "associated_with", "target:MAOB", 0.82),
            _rel("mt", "molecule:gen-risk", "targets", "target:MAOB", 0.75),
            _rel("tm", "target:MAOB", "has_mechanism", "mechanism:maob-inhibition", 0.8),
            _rel(
                "assay-neg",
                "assay_result:negative",
                "contradicts",
                "molecule:gen-risk",
                0.92,
                relation_type="experimental",
                metadata={"qc_status": "passed", "outcome_label": "negative"},
            ),
            _rel(
                "risk",
                "molecule:gen-risk",
                "has_developability_risk",
                "developability_alert:herg",
                0.82,
                relation_type="computational",
            ),
        ],
    )

    hypothesis = extract_mechanism_hypotheses(graph)[0]

    assert hypothesis.status == "contradicted"
    assert hypothesis.contradiction_score >= 0.6
    assert {"assay-neg", "risk"} <= set(hypothesis.contradiction_relation_ids)
    warning_text = " ".join(hypothesis.warnings)
    assert "Contradictions are surfaced" in warning_text
    assert "developability" in warning_text


def test_extract_unresolved_mechanism_from_unsupported_path() -> None:
    graph = _graph(
        [
            _entity("disease:pd", "disease", "Parkinson disease"),
            _entity("target:MAOB", "target", "MAOB"),
            _entity("molecule:untested", "molecule", "Untested candidate"),
            _entity("mechanism:maob-inhibition", "mechanism", "MAOB inhibition"),
        ],
        [
            _rel("dt", "disease:pd", "associated_with", "target:MAOB", 0.4),
            _rel(
                "mt-inferred",
                "molecule:untested",
                "targets",
                "target:MAOB",
                0.45,
                relation_type="inferred",
            ),
            _rel(
                "mm-inferred",
                "molecule:untested",
                "has_mechanism",
                "mechanism:maob-inhibition",
                0.4,
                relation_type="inferred",
            ),
        ],
    )

    hypothesis = extract_mechanism_hypotheses(graph)[0]

    assert hypothesis.status == "unresolved"
    assert hypothesis.support_score < 0.3
    assert hypothesis.contradiction_score == 0.0
    assert hypothesis.evidence_relation_ids == []


def test_extract_generated_mechanism_remains_hypothesis() -> None:
    graph = _graph(
        [
            _entity("disease:pd", "disease", "Parkinson disease"),
            _entity("target:MAOB", "target", "MAOB"),
            _entity("molecule:seed", "molecule", "Seed molecule"),
            _entity("generated_molecule:gen-1", "generated_molecule", "Generated MAOB 1"),
            _entity("mechanism:maob-inhibition", "mechanism", "MAOB inhibition"),
        ],
        [
            _rel("dt", "disease:pd", "associated_with", "target:MAOB", 0.82),
            _rel("seed-target", "molecule:seed", "targets", "target:MAOB", 0.78),
            _rel(
                "gen-target",
                "generated_molecule:gen-1",
                "hypothesizes",
                "target:MAOB",
                0.7,
                relation_type="inferred",
            ),
            _rel(
                "gen-mech",
                "generated_molecule:gen-1",
                "has_mechanism",
                "mechanism:maob-inhibition",
                0.6,
                relation_type="inferred",
            ),
            _rel(
                "lineage",
                "generated_molecule:gen-1",
                "generated_from",
                "molecule:seed",
                0.8,
                relation_type="generated_lineage",
            ),
            _rel(
                "no-direct-evidence",
                "generated_molecule:gen-1",
                "has_no_direct_evidence",
                "generated_molecule:gen-1",
                1.0,
                relation_type="inferred",
            ),
        ],
    )

    hypothesis = next(
        item
        for item in extract_mechanism_hypotheses(graph)
        if item.generated_molecule_entity_ids == ["generated_molecule:gen-1"]
    )

    assert hypothesis.status == "generated_hypothesis"
    assert hypothesis.generated_molecule_entity_ids == ["generated_molecule:gen-1"]
    assert hypothesis.novelty_score >= 0.6
    assert "Generated mechanisms remain hypotheses" in " ".join(hypothesis.warnings)


def _graph(entities: list[GraphEntity], relations: list[GraphRelation]) -> KnowledgeGraph:
    return KnowledgeGraph(graph_id="kg-mechanism-test", entities=entities, relations=relations)


def _entity(entity_id: str, entity_type: str, name: str) -> GraphEntity:
    return GraphEntity(entity_id=entity_id, entity_type=entity_type, name=name)


def _rel(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    confidence: float,
    *,
    relation_type: str = "evidence_backed",
    metadata: dict[str, object] | None = None,
) -> GraphRelation:
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=confidence,
        direction="contradictory" if predicate in {"contradicts", "failed_qc"} else "supportive",
        source_artifact_ids=[f"artifact:{relation_id}"],
        source_record_ids=[relation_id],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata=metadata or {},
    )
