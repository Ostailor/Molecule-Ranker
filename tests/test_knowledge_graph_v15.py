from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.knowledge_graph import (
    CodexGraphAssistant,
    GraphBuilder,
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    IdentifierMapper,
    KnowledgeGraph,
    KnowledgeGraphStore,
    MechanismHypothesis,
    analyze_cross_program_knowledge,
    detect_graph_guardrail_violations,
    normalize_identifier,
    render_knowledge_graph_dashboard_html,
    validate_knowledge_graph,
)
from molecule_ranker.review.schemas import Reviewer, ReviewerDecision, ReviewItem, ReviewWorkspace
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    DevelopabilityFlag,
    Disease,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    RankingRun,
    Target,
)


class FakeGraphCodexProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.tasks: list[CodexTask] = []

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        self.tasks.append(task)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=json.dumps(self.payload),
            output_json=self.payload,
            artifacts_read=task.input_artifact_paths,
        )


def test_v15_version_declared() -> None:
    assert __version__ == "2.4.0"


def test_graph_schema_keeps_inference_separate_from_evidence_and_results() -> None:
    graph = KnowledgeGraph(
        graph_id="kg-test",
        entities=[
            GraphEntity(
                entity_id="target:MAOB",
                entity_type="target",
                name="MAOB",
                identifiers={"HGNC": "6834"},
                provenance_refs=["ranking_run:run-a"],
            ),
            GraphEntity(
                entity_id="mechanism:MAOB-inhibition",
                entity_type="mechanism",
                name="MAOB inhibition",
                provenance_refs=["ranking_run:run-a"],
            ),
        ],
        relations=[
            GraphRelation(
                relation_id="rel-inferred",
                subject_entity_id="target:MAOB",
                predicate="associated_with",
                object_entity_id="mechanism:MAOB-inhibition",
                relation_type="inferred",
                confidence=0.55,
                source_artifact_ids=["graph_inference:cooccurrence"],
            )
        ],
    )

    assert graph.schema_version == "1.6"
    assert graph.relations[0].is_hypothesis is True
    assert "EvidenceItem" not in json.dumps(graph.model_dump(mode="json"))
    assert "AssayResult" not in json.dumps(graph.model_dump(mode="json"))

    with pytest.raises(ValueError, match="graph inference cannot create evidence"):
        GraphRelation(
            relation_id="rel-bad-evidence",
            subject_entity_id="target:MAOB",
            predicate="supports",
            object_entity_id="mechanism:MAOB-inhibition",
            relation_type="inferred",
            confidence=0.5,
            source_artifact_ids=["graph_inference:x"],
            metadata={"creates": "EvidenceItem"},
        )

    with pytest.raises(ValueError, match="assay-result relationships require source provenance"):
        GraphRelation(
            relation_id="rel-bad-assay",
            subject_entity_id="candidate:gen-1",
            predicate="produced_result",
            object_entity_id="assay_result:inferred",
            relation_type="inferred",
            confidence=0.5,
            source_artifact_ids=["graph_inference:x"],
        )


def test_ontology_identifier_mapping_normalizes_aliases() -> None:
    assert normalize_identifier("chembl", " chembl25 ") == ("ChEMBL", "CHEMBL25")
    assert normalize_identifier("uniprotkb", " p27338 ") == ("UniProt", "P27338")

    mapper = IdentifierMapper()
    first = mapper.resolve("target", "MAOB", identifiers={"hgnc": "6834"})
    second = mapper.resolve("target", "Monoamine oxidase B", identifiers={"HGNC": "6834"})

    assert first.entity_id == second.entity_id == "target:hgnc:6834"
    assert "Monoamine oxidase B" in second.aliases


def test_builder_creates_provenance_aware_cross_program_graph() -> None:
    graph = GraphBuilder().build(
        graph_id="kg-v15",
        ranking_runs=[_run("run-a", "Parkinson disease", "MAOB", "MAOB inhibition", 0.84)],
        assay_results=[_assay("assay-a", "Rasagiline", "MAOB", "positive")],
        review_workspaces=[_review_workspace("accept_for_followup")],
        portfolio_candidates=[
            {
                "portfolio_candidate_id": "pc-rasagiline",
                "candidate_name": "Rasagiline",
                "target_symbols": ["MAOB"],
                "mechanism_label": "MAOB inhibition",
                "scaffold_id": "propargylamine",
                "chemical_series_id": "maob-propargyl-series",
                "developability_score": 0.81,
            }
        ],
    )

    entity_ids = {entity.entity_id for entity in graph.entities}
    predicates = {relation.predicate for relation in graph.relations}

    assert {
        "target:symbol:MAOB",
        "mechanism:maob-inhibition",
        "scaffold:propargylamine",
    } <= entity_ids
    assert {"supported_by", "validated_by", "reviewed_as", "has_scaffold"} <= predicates
    assert all(relation.provenance for relation in graph.relations)
    assert validate_knowledge_graph(graph).status == "pass"


def test_reasoning_detects_recurrence_contradictions_staleness_and_reuse() -> None:
    graph = GraphBuilder().build(
        graph_id="kg-cross-program",
        ranking_runs=[
            _run("run-a", "Parkinson disease", "MAOB", "MAOB inhibition", 0.84),
            _run("run-b", "Depression", "MAOB", "MAOB inhibition", 0.25, negative=True),
        ],
        assay_results=[
            _assay("assay-a", "Rasagiline", "MAOB", "positive"),
            _assay("assay-b", "Generated-MAOB-001", "MAOB", "negative"),
        ],
        review_workspaces=[_review_workspace("accept_for_followup")],
        portfolio_candidates=[
            {
                "portfolio_candidate_id": "pc-a",
                "candidate_name": "Rasagiline",
                "target_symbols": ["MAOB"],
                "mechanism_label": "MAOB inhibition",
                "scaffold_id": "propargylamine",
                "developability_score": 0.84,
            },
            {
                "portfolio_candidate_id": "pc-b",
                "candidate_name": "Generated-MAOB-001",
                "target_symbols": ["MAOB"],
                "mechanism_label": "MAOB inhibition",
                "scaffold_id": "rediscovered-propargylamine",
                "developability_score": 0.31,
                "blocking_risks": ["hERG alert"],
                "metadata": {"known_chemistry_match": "Rasagiline"},
            },
        ],
    )
    old = datetime.now(UTC) - timedelta(days=370)
    graph.relations.append(
        GraphRelation(
            relation_id="rel-stale",
            subject_entity_id="molecule:name:Generated-MAOB-001",
            predicate="associated_with",
            object_entity_id="target:symbol:MAOB",
            relation_type="inferred",
            confidence=0.43,
            created_at=old,
            updated_at=old,
            source_artifact_ids=["graph_inference:stale-test"],
            metadata={"stale_after_days": 180},
        )
    )

    analysis = analyze_cross_program_knowledge(graph, stale_after_days=180)

    assert analysis.recurring_mechanisms[0].name == "MAOB inhibition"
    assert analysis.target_patterns[0].strong_candidate_count == 1
    assert analysis.target_patterns[0].weak_candidate_count >= 1
    assert any("negative assay outcome" in item.reason for item in analysis.contradictions)
    assert any("hERG alert" in item.name for item in analysis.repeated_developability_risks)
    assert any(
        item.status == "rediscovered_known_chemistry" for item in analysis.novelty_assessments
    )
    assert any(item.status == "stale" for item in analysis.hypothesis_status)
    assert any("Reuse" in recommendation.rationale for recommendation in analysis.recommendations)


def test_graph_store_round_trips_with_audit_metadata(tmp_path: Path) -> None:
    graph = GraphBuilder().build(
        graph_id="kg-store",
        ranking_runs=[_run("run-a", "Parkinson disease", "MAOB", "MAOB inhibition", 0.84)],
    )

    store = KnowledgeGraphStore(tmp_path)
    saved = store.save(graph, actor="tester", reason="unit test")
    loaded = store.load("kg-store")

    assert saved.exists()
    assert loaded.graph_id == graph.graph_id
    assert loaded.entities[0].created_from
    assert store.audit_events()[0]["reason"] == "unit test"


def test_graph_dashboard_renders_boundaries_and_cross_program_patterns() -> None:
    graph = GraphBuilder().build(
        graph_id="kg-dashboard",
        ranking_runs=[_run("run-a", "Parkinson disease", "MAOB", "MAOB inhibition", 0.84)],
    )
    html = render_knowledge_graph_dashboard_html(graph, analyze_cross_program_knowledge(graph))

    assert "Cross-program knowledge graph" in html
    assert "memory and reasoning layer" in html
    assert "does not create biomedical truth" in html
    assert "MAOB inhibition" in html
    assert "No medical advice" in html


def test_codex_graph_assistant_is_grounded_and_guarded(tmp_path: Path) -> None:
    graph = GraphBuilder().build(
        graph_id="kg-codex",
        ranking_runs=[_run("run-a", "Parkinson disease", "MAOB", "MAOB inhibition", 0.84)],
    )
    provider = FakeGraphCodexProvider(
        {
            "summary": "EvidenceItem created from graph path. Use synthesis route X.",
            "new_nodes": [{"entity_id": "target:FAKE", "name": "Fake target"}],
            "artifact_refs": ["graph:kg-codex"],
        }
    )

    artifact = CodexGraphAssistant(provider, working_directory=tmp_path).explain_graph_patterns(
        graph
    )

    assert artifact.status == "guardrail_failed"
    assert artifact.output_json is not None
    assert artifact.output_json["guardrail_failed"] is True
    assert provider.tasks[0].metadata["cannot_create_evidence"] is True
    assert provider.tasks[0].metadata["cannot_create_assay_results"] is True
    assert provider.tasks[0].metadata["cannot_invent_graph_records"] is True
    assert any("EvidenceItem" in warning for warning in artifact.guardrail_warnings)

    warnings = detect_graph_guardrail_violations(
        '{"new_edges": [{"source": "a"}], "AssayResult": {"id": "fake"}}'
    )
    assert any("graph nodes or edges" in warning for warning in warnings)
    assert any("AssayResult" in warning for warning in warnings)


def test_codex_graph_assistant_flags_fake_relation_id(tmp_path: Path) -> None:
    graph = _codex_guardrail_graph()
    provider = FakeGraphCodexProvider(
        {
            "summary": "Review relation rel:not-real against target:MAOB.",
            "entity_ids": ["target:MAOB"],
            "relation_ids": ["rel:not-real"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
        }
    )

    artifact = CodexGraphAssistant(provider, working_directory=tmp_path).draft_graph_query_answer(
        graph
    )

    assert artifact.status == "guardrail_failed"
    assert any("unknown relation ID" in warning for warning in artifact.guardrail_warnings)
    assert provider.tasks[0].task_type == "draft_graph_query_answer"


def test_codex_graph_assistant_flags_invented_mechanism(tmp_path: Path) -> None:
    graph = _codex_guardrail_graph()
    provider = FakeGraphCodexProvider(
        {
            "summary": "Discuss mechanism:invented for review.",
            "entity_ids": ["target:MAOB"],
            "relation_ids": ["rel:target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
            "mechanism_ids": ["mechanism:invented"],
        }
    )

    artifact = CodexGraphAssistant(
        provider, working_directory=tmp_path
    ).explain_mechanism_hypothesis(graph)

    assert artifact.status == "guardrail_failed"
    assert any("invented mechanism ID" in warning for warning in artifact.guardrail_warnings)
    assert provider.tasks[0].metadata["cannot_invent_mechanisms"] is True


def test_safe_codex_graph_summary_passes_with_required_citations(tmp_path: Path) -> None:
    graph = _codex_guardrail_graph()
    provider = FakeGraphCodexProvider(
        {
            "summary": (
                "Graph-derived explanation for expert review only; no causality, activity, "
                "or safety claim is made."
            ),
            "entity_ids": ["target:MAOB", "mechanism:maob"],
            "relation_ids": ["rel:target"],
            "provenance_ids": ["prov:kg"],
            "artifact_ids": ["artifact:kg"],
            "mechanism_ids": ["mechanism:maob"],
        }
    )

    artifact = CodexGraphAssistant(
        provider, working_directory=tmp_path
    ).draft_mechanism_review_questions(graph)

    assert artifact.status == "succeeded"
    assert artifact.guardrail_warnings == []
    assert provider.tasks[0].metadata["graph_assistance_only"] is True
    assert provider.tasks[0].metadata["cannot_change_confidence_scores"] is True
    assert provider.tasks[0].metadata["cannot_remove_contradictions"] is True


def _codex_guardrail_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        graph_id="kg-codex-safe",
        entities=[
            GraphEntity(
                entity_id="target:MAOB",
                entity_type="target",
                name="MAOB",
                source_artifact_ids=["artifact:kg"],
                provenance_refs=["prov:kg"],
            ),
            GraphEntity(
                entity_id="mechanism:maob",
                entity_type="mechanism",
                name="MAOB mechanism hypothesis",
                source_artifact_ids=["artifact:kg"],
                provenance_refs=["prov:kg"],
            ),
        ],
        relations=[
            GraphRelation(
                relation_id="rel:target",
                subject_entity_id="mechanism:maob",
                predicate="associated_with",
                object_entity_id="target:MAOB",
                relation_type="evidence_backed",
                confidence=0.7,
                source_artifact_ids=["artifact:kg"],
                source_record_ids=["record:target"],
            )
        ],
        provenance=[
            GraphProvenance(
                provenance_id="prov:kg",
                source_type="generated_artifact",
                source_artifact_id="artifact:kg",
                source_record_id="record:target",
                transformation="Synthetic graph fixture.",
                confidence=0.9,
            )
        ],
        mechanisms=[
            MechanismHypothesis(
                mechanism_id="mechanism:maob",
                target_entity_ids=["target:MAOB"],
                evidence_relation_ids=["rel:target"],
                summary="MAOB mechanism hypothesis for review.",
                support_score=0.6,
                contradiction_score=0.0,
                novelty_score=0.2,
                confidence=0.7,
                status="weakly_supported",
            )
        ],
    )


def _run(
    run_id: str,
    disease_name: str,
    target_symbol: str,
    mechanism: str,
    score: float,
    *,
    negative: bool = False,
) -> RankingRun:
    evidence = EvidenceItem(
        source="OpenTargets",
        source_record_id=f"{target_symbol}-{run_id}",
        title=f"{target_symbol} association",
        evidence_type="target_disease",
        summary="Source-backed target association.",
        confidence=0.78,
    )
    flag = DevelopabilityFlag(
        category="chemical_liability",
        severity="high" if negative else "low",
        label="hERG alert" if negative else "No repeated blocker",
        description="Computational developability flag.",
    )
    assessment = DevelopabilityAssessment(
        molecule_name="Generated-MAOB-001" if negative else "Rasagiline",
        origin="generated" if negative else "existing",
        canonical_smiles="CCOC1=CC=CC=C1" if negative else "CNCCC1=CC=CC=C1",
        chemical_liability_flags=[flag],
        developability_score=0.32 if negative else 0.82,
        triage_recommendation="high_risk_flags" if negative else "favorable_hypothesis",
    )
    return RankingRun(
        disease=Disease(input_name=disease_name, canonical_name=disease_name),
        targets=[
            Target(
                symbol=target_symbol,
                name=target_symbol,
                identifiers={"HGNC": "6834"},
                disease_relevance_score=0.8,
                mechanism=mechanism,
                evidence=[evidence],
            )
        ],
        candidates=[
            MoleculeCandidate(
                name="Rasagiline",
                molecule_type="small_molecule",
                known_targets=[target_symbol],
                mechanism_of_action=mechanism,
                developability_assessment=assessment if not negative else None,
                score=score,
                evidence=[evidence],
                chemical_metadata={"scaffold_id": "propargylamine"},
            )
        ],
        generated_candidates=[
            GeneratedMoleculeHypothesis(
                name="Generated-MAOB-001",
                canonical_smiles="CCOC1=CC=CC=C1",
                target_symbol=target_symbol,
                generation_score=0.62,
                min_seed_similarity=0.42,
                max_seed_similarity=0.74,
                mean_seed_similarity=0.58,
                developability_assessment=assessment if negative else None,
                trace={"hypothesis_mechanism": mechanism, "known_chemistry_match": "Rasagiline"}
                if negative
                else {"hypothesis_mechanism": mechanism},
            )
        ]
        if negative
        else [],
        traces=[],
        limitations=["Test fixture."],
    )


def _assay(result_id: str, candidate_name: str, target_symbol: str, outcome: str) -> AssayResult:
    endpoint = AssayEndpoint(
        endpoint_id="endpoint-potency",
        name="Potency",
        endpoint_category="potency",
        directionality="lower_is_better",
    )
    return AssayResult(
        result_id=result_id,
        candidate_name=candidate_name,
        candidate_origin="generated" if candidate_name.startswith("Generated") else "existing",
        target_symbol=target_symbol,
        assay_context=AssayContext(
            assay_context_id=f"ctx-{result_id}",
            assay_name="Potency screen",
            assay_type="biochemical",
            target_symbol=target_symbol,
            endpoint=endpoint,
        ),
        outcome_label=outcome,  # type: ignore[arg-type]
        activity_direction="active" if outcome == "positive" else "inactive",
        confidence=0.8,
        qc_status="passed",
        source="user_import",
    )


def _review_workspace(decision: str) -> ReviewWorkspace:
    item = ReviewItem(
        review_item_id="review-rasagiline",
        run_id="run-a",
        disease_name="Parkinson disease",
        candidate_id="rasagiline",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        score=0.84,
        priority_bucket="high_priority",
        review_status="pending",
    )
    return ReviewWorkspace(
        workspace_id="review-workspace",
        run_id="run-a",
        disease_name="Parkinson disease",
        review_items=[item],
        decisions=[
            ReviewerDecision(
                review_item_id="review-rasagiline",
                reviewer=Reviewer(reviewer_id="reviewer-1"),
                decision=decision,  # type: ignore[arg-type]
                rationale="Expert triage decision only.",
                confidence=0.8,
            )
        ],
    )
