from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.knowledge_graph import GraphBuilder


def test_graph_builder_ingests_synthetic_artifacts_with_guardrails(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "candidates.json",
        {
            "project_id": "project-parkinson",
            "program_id": "program-maob",
            "disease": {"name": "Parkinson disease", "identifiers": {"EFO": "EFO_0002508"}},
            "targets": [
                {
                    "symbol": "MAOB",
                    "identifiers": {"HGNC": "6834"},
                    "evidence": [{"source_record_id": "ot-maob", "confidence": 0.82}],
                }
            ],
            "candidates": [
                {
                    "candidate_id": "cand-rasagiline",
                    "name": "Rasagiline",
                    "identifiers": {"ChEMBLMolecule": "CHEMBL887"},
                    "known_targets": ["MAOB"],
                    "mechanism_of_action": "MAOB inhibition",
                    "score": 0.84,
                    "direct_evidence_available": True,
                    "chemical_metadata": {"scaffold_id": "propargylamine"},
                }
            ],
        },
    )
    _write_json(
        tmp_path / "generated_candidates.json",
        {
            "retained_generated_molecules": [
                {
                    "generated_id": "gen-maob-1",
                    "name": "Generated-MAOB-001",
                    "canonical_smiles": "CCOC1=CC=CC=C1",
                    "target_symbol": "MAOB",
                    "generation_score": 0.91,
                    "seed_molecule_name": "Rasagiline",
                    "trace": {"hypothesis_mechanism": "MAOB inhibition"},
                }
            ]
        },
    )
    _write_json(
        tmp_path / "developability.json",
        {
            "assessments": [
                {
                    "candidate_name": "Generated-MAOB-001",
                    "risk_level": "high",
                    "blocking_risks": ["hERG alert"],
                    "developability_score": 0.22,
                }
            ]
        },
    )
    _write_json(
        tmp_path / "experimental_results.json",
        {
            "results": [
                {
                    "result_id": "assay-pass",
                    "candidate_name": "Rasagiline",
                    "target_symbol": "MAOB",
                    "assay_name": "MAOB potency",
                    "outcome_label": "positive",
                    "qc_status": "passed",
                    "confidence": 0.88,
                },
                {
                    "result_id": "assay-failed-qc",
                    "candidate_name": "Generated-MAOB-001",
                    "target_symbol": "MAOB",
                    "assay_name": "MAOB potency",
                    "outcome_label": "positive",
                    "qc_status": "failed",
                    "confidence": 0.4,
                },
            ]
        },
    )
    _write_json(
        tmp_path / "model_predictions.json",
        {
            "predictions": [
                {
                    "prediction_id": "pred-gen-1",
                    "candidate_name": "Generated-MAOB-001",
                    "model_name": "surrogate-maob",
                    "score": 0.78,
                }
            ]
        },
    )
    _write_json(
        tmp_path / "structure_aware_assessments.json",
        {
            "structure_aware_assessments": [
                {
                    "assessment_id": "struct-gen-1",
                    "candidate_name": "Generated-MAOB-001",
                    "pdb_id": "2V5Z",
                    "docking_pose_id": "pose-gen-1",
                    "priority_score": 0.72,
                }
            ]
        },
    )
    _write_json(
        tmp_path / "review_queue.json",
        {
            "review_items": [
                {
                    "review_item_id": "review-gen-1",
                    "candidate_name": "Generated-MAOB-001",
                    "candidate_id": "gen-maob-1",
                    "target_symbols": ["MAOB"],
                }
            ],
            "decisions": [
                {
                    "decision_id": "decision-gen-1",
                    "review_item_id": "review-gen-1",
                    "decision": "needs_experiment",
                    "confidence": 0.7,
                }
            ],
        },
    )
    _write_json(
        tmp_path / "portfolio_optimization.json",
        {
            "portfolio_id": "portfolio-q1",
            "selected_candidates": [{"candidate_name": "Rasagiline"}],
        },
    )

    graph = GraphBuilder().build_from_directory(tmp_path, graph_id="kg-artifacts")
    predicates = {relation.predicate for relation in graph.relations}
    entities = {entity.entity_id: entity for entity in graph.entities}

    assert "disease:efo:efo_0002508" in entities
    assert "target:hgnc:6834" in entities
    assert "generated_molecule:generatedmolecule:gen-maob-1" in entities
    assert {
        "associated_with",
        "targets",
        "has_scaffold",
        "tested_in",
        "supports",
        "failed_qc",
        "generated_from",
        "has_no_direct_evidence",
        "has_developability_risk",
        "selected_in_portfolio",
        "reviewed_as",
        "predicted_by_model",
        "computational_pose_for",
        "computational_prioritization_for",
    } <= predicates
    assert {"binds", "active", "safe"}.isdisjoint(predicates)
    assert not any(
        relation.predicate == "supports" and relation.metadata.get("qc_status") == "failed"
        for relation in graph.relations
    )
    assert any(
        relation.predicate == "failed_qc" and relation.metadata.get("qc_status") == "failed"
        for relation in graph.relations
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
