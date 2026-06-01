from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.knowledge_graph.mechanism import extract_mechanism_hypotheses
from molecule_ranker.knowledge_graph.ontology import normalize_identifier
from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphRelation,
    KnowledgeGraph,
    ProvenanceSource,
    make_entity_id,
)
from molecule_ranker.review.schemas import ReviewWorkspace
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    RankingRun,
    Target,
)


class GraphBuilder:
    """Builds a provenance-aware graph from existing molecule-ranker artifacts."""

    ARTIFACT_FILENAMES = (
        "candidates.json",
        "generated_candidates.json",
        "literature_evidence.json",
        "developability.json",
        "experimental_results.json",
        "experimental_evidence.json",
        "active_learning_batch.json",
        "review_queue.json",
        "model_predictions.json",
        "structure_aware_assessments.json",
        "portfolio_optimization.json",
        "integration_sync.json",
        "trace.json",
        "artifact_manifest.json",
    )

    def build(
        self,
        *,
        graph_id: str,
        ranking_runs: list[RankingRun] | None = None,
        assay_results: list[AssayResult] | None = None,
        review_workspaces: list[ReviewWorkspace] | None = None,
        portfolio_candidates: list[dict[str, Any]] | None = None,
        artifact_dir: str | Path | None = None,
        artifact_paths: list[str | Path] | dict[str, str | Path] | None = None,
        artifact_payloads: dict[str, Any] | None = None,
    ) -> KnowledgeGraph:
        self._entities: dict[str, GraphEntity] = {}
        self._relations: dict[str, GraphRelation] = {}
        for index, run in enumerate(ranking_runs or []):
            self._add_ranking_run(run, run_id=f"run-{index + 1}")
        for result in assay_results or []:
            self._add_assay_result(result)
        for workspace in review_workspaces or []:
            self._add_review_workspace(workspace)
        for candidate in portfolio_candidates or []:
            self._add_portfolio_candidate(candidate)
        for artifact_name, payload in self._load_artifacts(
            artifact_dir=artifact_dir,
            artifact_paths=artifact_paths,
            artifact_payloads=artifact_payloads,
        ).items():
            self._add_artifact_payload(artifact_name, payload)
        graph = KnowledgeGraph(
            graph_id=graph_id,
            entities=sorted(self._entities.values(), key=lambda entity: entity.entity_id),
            relations=sorted(self._relations.values(), key=lambda relation: relation.relation_id),
        )
        graph.mechanisms = extract_mechanism_hypotheses(graph)
        return graph

    def build_from_directory(self, artifact_dir: str | Path, *, graph_id: str) -> KnowledgeGraph:
        return self.build(graph_id=graph_id, artifact_dir=artifact_dir)

    def build_from_artifacts(
        self,
        *,
        graph_id: str,
        artifact_paths: list[str | Path] | dict[str, str | Path] | None = None,
        artifact_payloads: dict[str, Any] | None = None,
    ) -> KnowledgeGraph:
        return self.build(
            graph_id=graph_id,
            artifact_paths=artifact_paths,
            artifact_payloads=artifact_payloads,
        )

    def _load_artifacts(
        self,
        *,
        artifact_dir: str | Path | None,
        artifact_paths: list[str | Path] | dict[str, str | Path] | None,
        artifact_payloads: dict[str, Any] | None,
    ) -> dict[str, Any]:
        loaded: dict[str, Any] = {}
        if artifact_payloads:
            loaded.update(artifact_payloads)
        if artifact_dir is not None:
            directory = Path(artifact_dir)
            for filename in self.ARTIFACT_FILENAMES:
                path = directory / filename
                if path.exists():
                    loaded[filename] = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(artifact_paths, dict):
            for artifact_name, path in artifact_paths.items():
                loaded[artifact_name] = json.loads(Path(path).read_text(encoding="utf-8"))
        else:
            for path_value in artifact_paths or []:
                path = Path(path_value)
                loaded[path.name] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def _add_artifact_payload(self, artifact_name: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        match artifact_name:
            case "candidates.json":
                self._add_candidates_artifact(payload, artifact_name)
            case "generated_candidates.json":
                self._add_generated_candidates_artifact(payload, artifact_name)
            case "literature_evidence.json":
                self._add_literature_artifact(payload, artifact_name)
            case "developability.json":
                self._add_developability_artifact(payload, artifact_name)
            case "experimental_results.json" | "experimental_evidence.json":
                self._add_experimental_artifact(payload, artifact_name)
            case "review_queue.json":
                self._add_review_artifact(payload, artifact_name)
            case "model_predictions.json":
                self._add_model_predictions_artifact(payload, artifact_name)
            case "structure_aware_assessments.json":
                self._add_structure_artifact(payload, artifact_name)
            case "portfolio_optimization.json":
                self._add_portfolio_artifact(payload, artifact_name)
            case "active_learning_batch.json":
                self._add_active_learning_artifact(payload, artifact_name)
            case "integration_sync.json" | "trace.json" | "artifact_manifest.json":
                self._add_manifest_artifact(payload, artifact_name)

    def _add_candidates_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        self._add_project_program(payload, source_id)
        disease_entity = self._disease_from_payload(payload, source_id)
        for target_record in _records(payload, "targets", "target_records"):
            target_entity = self._target_from_record(target_record, source_id)
            if disease_entity is not None:
                self._relation(
                    disease_entity.entity_id,
                    target_entity.entity_id,
                    "associated_with",
                    "source_backed",
                    _confidence(target_record, default=0.8),
                    [ProvenanceSource(source_type="artifact", source_id=source_id)],
                    metadata={"source_artifact": source_id},
                )
            for evidence in _records(target_record, "evidence", "evidence_items"):
                self._add_evidence_record(target_entity.entity_id, evidence, source_id)
        for candidate_record in _records(payload, "candidates", "ranked_candidates", "molecules"):
            self._candidate_from_record(candidate_record, source_id)
        for generated_record in _records(payload, "generated_molecule_hypotheses"):
            self._generated_from_record(generated_record, source_id)

    def _add_generated_candidates_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(
            payload,
            "retained_generated_molecules",
            "generated_candidates",
            "generated_molecule_hypotheses",
            "rejected_generated_molecules",
        ):
            self._generated_from_record(record, source_id)

    def _add_literature_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(payload, "evidence", "literature_evidence", "claims", "items"):
            subject_name = _first_text(record, "candidate_name", "target_symbol", "mechanism_label")
            if not subject_name:
                continue
            subject_type = "target" if record.get("target_symbol") else "molecule"
            subject = self._entity(
                subject_type,
                subject_name,
                namespace="symbol" if subject_type == "target" else "name",
                provenance=ProvenanceSource(source_type="literature_artifact", source_id=source_id),
            )
            self._add_evidence_record(subject.entity_id, record, source_id)

    def _add_developability_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(payload, "assessments", "developability_assessments", "items"):
            candidate_name = _first_text(record, "candidate_name", "molecule_name", "name")
            if not candidate_name:
                continue
            candidate = self._entity(
                "molecule",
                candidate_name,
                provenance=ProvenanceSource(
                    source_type="developability_artifact", source_id=source_id
                ),
                metadata={
                    "developability_score": _optional_float(record.get("developability_score"))
                },
            )
            risks = _risk_labels(record)
            risk_level = str(record.get("risk_level") or "").lower()
            if risk_level in {"high", "critical"} and not risks:
                risks.append(f"{risk_level}_developability_risk")
            for risk in risks:
                self._link_developability_alert(
                    candidate.entity_id,
                    risk,
                    source_id,
                    confidence=0.8 if risk_level != "critical" else 0.95,
                    metadata={
                        "risk_level": risk_level or None,
                        "developability_score": _optional_float(record.get("developability_score")),
                    },
                )

    def _add_experimental_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(
            payload, "results", "experimental_results", "assay_results", "items"
        ):
            self._experimental_result_from_record(record, source_id)

    def _add_review_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        items = {
            str(
                item.get("review_item_id") or item.get("candidate_id") or item.get("candidate_name")
            ): item
            for item in _records(payload, "review_items", "items")
        }
        for decision in _records(payload, "decisions", "review_decisions"):
            item = items.get(str(decision.get("review_item_id"))) or items.get(
                str(decision.get("candidate_id") or decision.get("candidate_name"))
            )
            candidate_name = _first_text(decision, "candidate_name") or (
                _first_text(item, "candidate_name", "name") if item else None
            )
            if not candidate_name:
                continue
            decision_id = str(
                decision.get("decision_id")
                or decision.get("review_decision_id")
                or f"{source_id}:{candidate_name}"
            )
            decision_entity = self._entity(
                "review_decision",
                decision_id,
                namespace="decision",
                provenance=ProvenanceSource(source_type="review_artifact", source_id=source_id),
                metadata={
                    "decision": decision.get("decision"),
                    "confidence": _optional_float(decision.get("confidence")),
                },
            )
            candidate = self._entity(
                "molecule",
                candidate_name,
                provenance=ProvenanceSource(source_type="review_artifact", source_id=source_id),
            )
            self._relation(
                decision_entity.entity_id,
                candidate.entity_id,
                "reviewed_as",
                "source_backed",
                _confidence(decision, default=0.7),
                [ProvenanceSource(source_type="review_decision", source_id=decision_id)],
                metadata={"decision": decision.get("decision"), "source_artifact": source_id},
            )

    def _add_model_predictions_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(payload, "predictions", "model_predictions", "items"):
            candidate_name = _first_text(record, "candidate_name", "molecule_name", "name")
            if not candidate_name:
                continue
            prediction_id = str(
                record.get("prediction_id")
                or record.get("model_prediction_id")
                or f"{source_id}:{candidate_name}"
            )
            prediction = self._entity(
                "model_prediction",
                prediction_id,
                namespace="prediction",
                provenance=ProvenanceSource(source_type="model_prediction", source_id=source_id),
                metadata={
                    "model_name": record.get("model_name"),
                    "score": _optional_float(record.get("score"), record.get("prediction_score")),
                    "not_evidence": True,
                },
            )
            candidate = self._entity(
                "molecule",
                candidate_name,
                provenance=ProvenanceSource(source_type="model_prediction", source_id=source_id),
            )
            self._relation(
                prediction.entity_id,
                candidate.entity_id,
                "predicted_by_model",
                "source_backed",
                _confidence(record, default=0.6),
                [ProvenanceSource(source_type="model_prediction", source_id=prediction_id)],
                metadata={"not_evidence": True, "source_artifact": source_id},
            )

    def _add_structure_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(
            payload,
            "structure_aware_assessments",
            "assessments",
            "items",
            "records",
        ):
            candidate_name = _first_text(record, "candidate_name", "molecule_name", "name")
            if not candidate_name:
                continue
            candidate = self._entity(
                "molecule",
                candidate_name,
                provenance=ProvenanceSource(source_type="structure_artifact", source_id=source_id),
            )
            if record.get("docking_pose_id") or record.get("pose_id"):
                pose_id = str(record.get("docking_pose_id") or record.get("pose_id"))
                pose = self._entity(
                    "docking_pose",
                    pose_id,
                    namespace="pose",
                    provenance=ProvenanceSource(
                        source_type="structure_artifact", source_id=source_id
                    ),
                    metadata={
                        "pdb_id": record.get("pdb_id"),
                        "not_binding_evidence": True,
                    },
                )
                self._relation(
                    pose.entity_id,
                    candidate.entity_id,
                    "computational_pose_for",
                    "source_backed",
                    _confidence(record, default=0.5),
                    [ProvenanceSource(source_type="structure_assessment", source_id=pose_id)],
                    metadata={"not_binding_evidence": True, "source_artifact": source_id},
                )
            assessment_id = str(record.get("assessment_id") or f"{source_id}:{candidate_name}")
            structure = self._entity(
                "structure",
                assessment_id,
                namespace="assessment",
                provenance=ProvenanceSource(source_type="structure_artifact", source_id=source_id),
                metadata={
                    "pdb_id": record.get("pdb_id"),
                    "priority_score": record.get("priority_score"),
                },
            )
            self._relation(
                structure.entity_id,
                candidate.entity_id,
                "computational_prioritization_for",
                "source_backed",
                _confidence(record, default=0.5),
                [ProvenanceSource(source_type="structure_assessment", source_id=assessment_id)],
                metadata={"not_activity_evidence": True, "source_artifact": source_id},
            )

    def _add_portfolio_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        portfolio_id = str(
            payload.get("portfolio_id") or payload.get("optimization_id") or source_id
        )
        portfolio = self._entity(
            "portfolio",
            portfolio_id,
            namespace="portfolio",
            provenance=ProvenanceSource(source_type="portfolio_artifact", source_id=source_id),
            metadata={"objective": payload.get("objective")},
        )
        for record in _records(
            payload, "selected_candidates", "portfolio_candidates", "candidates"
        ):
            candidate_name = _first_text(record, "candidate_name", "name")
            if not candidate_name:
                continue
            candidate = self._entity(
                "molecule",
                candidate_name,
                provenance=ProvenanceSource(source_type="portfolio_artifact", source_id=source_id),
            )
            self._relation(
                candidate.entity_id,
                portfolio.entity_id,
                "selected_in_portfolio",
                "source_backed",
                _confidence(record, default=0.8),
                [ProvenanceSource(source_type="portfolio_selection", source_id=portfolio_id)],
                metadata={"source_artifact": source_id},
            )

    def _add_active_learning_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        for record in _records(payload, "selected_candidates", "batch", "items", "recommendations"):
            candidate_name = _first_text(record, "candidate_name", "name")
            if candidate_name:
                self._entity(
                    "molecule",
                    candidate_name,
                    provenance=ProvenanceSource(
                        source_type="active_learning_batch", source_id=source_id
                    ),
                    metadata={"active_learning_priority": record.get("priority_score")},
                )

    def _add_manifest_artifact(self, payload: dict[str, Any], source_id: str) -> None:
        self._add_project_program(payload, source_id)
        for artifact in _records(payload, "artifacts", "artifact_manifest", "files"):
            artifact_id = _first_text(artifact, "artifact_id", "path", "name")
            if artifact_id:
                self._entity(
                    "project",
                    artifact_id,
                    namespace="artifact",
                    provenance=ProvenanceSource(
                        source_type="artifact_manifest", source_id=source_id
                    ),
                )

    def _add_ranking_run(self, run: RankingRun, *, run_id: str) -> None:
        run_id = _run_id(run, run_id)
        disease = self._entity(
            "disease",
            run.disease.canonical_name,
            provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
            identifiers=run.disease.identifiers,
        )
        for target in run.targets:
            target_entity = self._add_target(target, run_id=run_id)
            self._relation(
                disease.entity_id,
                target_entity.entity_id,
                "associated_with",
                "source_backed",
                0.8,
                [ProvenanceSource(source_type="ranking_run", source_id=run_id)],
            )
        for candidate in run.candidates:
            candidate_entity = self._add_candidate(candidate, run_id=run_id)
            for target_symbol in candidate.known_targets:
                target_entity = self._entity(
                    "target",
                    target_symbol,
                    namespace="symbol",
                    provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
                )
                self._relation(
                    candidate_entity.entity_id,
                    target_entity.entity_id,
                    "targets",
                    "source_backed",
                    candidate.score or 0.5,
                    [ProvenanceSource(source_type="ranking_run", source_id=run_id)],
                    metadata={"candidate_score": candidate.score},
                )
            if candidate.mechanism_of_action:
                self._link_mechanism(
                    candidate_entity.entity_id, candidate.mechanism_of_action, run_id
                )
            if candidate.chemical_metadata.get("scaffold_id"):
                self._link_scaffold(
                    candidate_entity.entity_id,
                    str(candidate.chemical_metadata["scaffold_id"]),
                    run_id,
                )
            self._add_developability(
                candidate_entity.entity_id, candidate.developability_assessment, run_id
            )
            for evidence in candidate.evidence:
                self._add_evidence(candidate_entity.entity_id, evidence, run_id)
        for generated in run.generated_candidates:
            self._add_generated(generated, run_id=run_id)

    def _add_target(self, target: Target, *, run_id: str) -> GraphEntity:
        target_entity = self._entity(
            "target",
            target.symbol,
            namespace="symbol",
            provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
            identifiers=target.identifiers,
            metadata={"disease_relevance_score": target.disease_relevance_score},
        )
        if target.mechanism:
            self._link_mechanism(target_entity.entity_id, target.mechanism, run_id)
        for evidence in target.evidence:
            self._add_evidence(target_entity.entity_id, evidence, run_id)
        return target_entity

    def _add_candidate(self, candidate: MoleculeCandidate, *, run_id: str) -> GraphEntity:
        entity = self._entity(
            "molecule",
            candidate.name,
            provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
            identifiers=candidate.identifiers,
            metadata={
                "origin": candidate.origin,
                "score": candidate.score,
                "direct_evidence_available": candidate.direct_evidence_available,
            },
        )
        return entity

    def _add_generated(self, generated: GeneratedMoleculeHypothesis, *, run_id: str) -> None:
        entity = self._entity(
            "generated_molecule",
            generated.name,
            provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
            identifiers={"canonical_smiles": generated.canonical_smiles},
            metadata={
                "origin": "generated",
                "generation_score": generated.generation_score,
                "known_chemistry_match": generated.trace.get("known_chemistry_match"),
            },
        )
        target = self._entity(
            "target",
            generated.target_symbol,
            namespace="symbol",
            provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
        )
        self._relation(
            entity.entity_id,
            target.entity_id,
            "hypothesizes",
            "graph_inferred",
            generated.generation_score,
            [ProvenanceSource(source_type="graph_inference", source_id=f"{run_id}:generation")],
            metadata={"generation_artifact_backed": True},
        )
        self._relation(
            entity.entity_id,
            entity.entity_id,
            "has_no_direct_evidence",
            "graph_inferred",
            1.0,
            [
                ProvenanceSource(
                    source_type="graph_inference", source_id=f"{run_id}:no_direct_evidence"
                )
            ],
            metadata={
                "inferred_relation": True,
                "not_evidence": True,
                "reason": "generated molecule without exact imported experimental result",
            },
        )
        mechanism = generated.trace.get("hypothesis_mechanism")
        if mechanism:
            self._link_mechanism(
                entity.entity_id, str(mechanism), run_id, assertion_type="graph_inferred"
            )
        self._add_developability(entity.entity_id, generated.developability_assessment, run_id)
        if generated.trace.get("known_chemistry_match"):
            known = self._entity(
                "molecule",
                str(generated.trace["known_chemistry_match"]),
                provenance=ProvenanceSource(source_type="ranking_run", source_id=run_id),
            )
            self._relation(
                entity.entity_id,
                known.entity_id,
                "novel_vs_known",
                "source_backed",
                0.8,
                [ProvenanceSource(source_type="ranking_run", source_id=run_id)],
                metadata={"status": "rediscovered_known_chemistry"},
            )

    def _add_assay_result(self, result: AssayResult) -> None:
        result_entity = self._entity(
            "assay_result",
            result.result_id,
            namespace="assay_result",
            provenance=ProvenanceSource(source_type="assay_result", source_id=result.result_id),
            metadata={
                "candidate_name": result.candidate_name,
                "outcome_label": result.outcome_label,
                "activity_direction": result.activity_direction,
            },
        )
        candidate = self._entity(
            "molecule",
            result.candidate_name,
            provenance=ProvenanceSource(source_type="assay_result", source_id=result.result_id),
        )
        assay = self._entity(
            "assay",
            result.assay_context.assay_name,
            namespace="assay",
            provenance=ProvenanceSource(source_type="assay_result", source_id=result.result_id),
            metadata={
                "assay_type": result.assay_context.assay_type,
                "target_symbol": result.target_symbol,
            },
        )
        self._relation(
            candidate.entity_id,
            assay.entity_id,
            "tested_in",
            "source_backed",
            result.confidence,
            [ProvenanceSource(source_type="assay_result", source_id=result.result_id)],
            metadata={"qc_status": result.qc_status},
        )
        if result.qc_status == "failed":
            self._relation(
                result_entity.entity_id,
                candidate.entity_id,
                "failed_qc",
                "source_backed",
                result.confidence,
                [ProvenanceSource(source_type="assay_result", source_id=result.result_id)],
                metadata={
                    "qc_status": result.qc_status,
                    "outcome_label": result.outcome_label,
                    "target_symbol": result.target_symbol,
                },
            )
            return
        relation_type = "validated_by" if result.outcome_label == "positive" else "contradicted_by"
        self._relation(
            candidate.entity_id,
            result_entity.entity_id,
            relation_type,
            "source_backed",
            result.confidence,
            [ProvenanceSource(source_type="assay_result", source_id=result.result_id)],
            metadata={
                "outcome_label": result.outcome_label,
                "target_symbol": result.target_symbol,
                "qc_status": result.qc_status,
            },
        )
        self._relation(
            result_entity.entity_id,
            candidate.entity_id,
            "supports" if result.outcome_label == "positive" else "contradicts",
            "source_backed",
            result.confidence,
            [ProvenanceSource(source_type="assay_result", source_id=result.result_id)],
            metadata={
                "outcome_label": result.outcome_label,
                "target_symbol": result.target_symbol,
                "qc_status": result.qc_status,
            },
        )
        if result.target_symbol:
            target = self._entity(
                "target",
                result.target_symbol,
                namespace="symbol",
                provenance=ProvenanceSource(source_type="assay_result", source_id=result.result_id),
            )
            self._relation(
                result_entity.entity_id,
                target.entity_id,
                "targets",
                "source_backed",
                result.confidence,
                [ProvenanceSource(source_type="assay_result", source_id=result.result_id)],
            )

    def _add_review_workspace(self, workspace: ReviewWorkspace) -> None:
        for decision in workspace.decisions:
            item = next(
                (
                    item
                    for item in workspace.review_items
                    if item.review_item_id == decision.review_item_id
                ),
                None,
            )
            if item is None:
                continue
            decision_entity = self._entity(
                "review_decision",
                decision.decision_id,
                namespace="decision",
                provenance=ProvenanceSource(
                    source_type="review_decision", source_id=decision.decision_id
                ),
                metadata={"decision": decision.decision, "confidence": decision.confidence},
            )
            candidate = self._entity(
                "molecule",
                item.candidate_name,
                provenance=ProvenanceSource(
                    source_type="review_decision", source_id=decision.decision_id
                ),
            )
            self._relation(
                candidate.entity_id,
                decision_entity.entity_id,
                "reviewed_as",
                "source_backed",
                decision.confidence,
                [ProvenanceSource(source_type="review_decision", source_id=decision.decision_id)],
                metadata={"decision": decision.decision},
            )

    def _add_portfolio_candidate(self, candidate: dict[str, Any]) -> None:
        source_id = str(candidate.get("portfolio_candidate_id") or candidate.get("candidate_name"))
        entity = self._entity(
            "molecule",
            str(candidate["candidate_name"]),
            provenance=ProvenanceSource(source_type="portfolio_candidate", source_id=source_id),
            metadata={
                "portfolio_candidate_id": source_id,
                "developability_score": candidate.get("developability_score"),
                "known_chemistry_match": candidate.get("metadata", {}).get("known_chemistry_match")
                if isinstance(candidate.get("metadata"), dict)
                else None,
            },
        )
        for target_symbol in candidate.get("target_symbols") or []:
            target = self._entity(
                "target",
                str(target_symbol),
                namespace="symbol",
                provenance=ProvenanceSource(source_type="portfolio_candidate", source_id=source_id),
            )
            self._relation(
                entity.entity_id,
                target.entity_id,
                "targets",
                "source_backed",
                float(candidate.get("developability_score") or 0.5),
                [ProvenanceSource(source_type="portfolio_candidate", source_id=source_id)],
                metadata={"candidate_score": candidate.get("developability_score")},
            )
        if candidate.get("mechanism_label"):
            self._link_mechanism(entity.entity_id, str(candidate["mechanism_label"]), source_id)
        if candidate.get("scaffold_id"):
            self._link_scaffold(entity.entity_id, str(candidate["scaffold_id"]), source_id)
        if candidate.get("chemical_series_id"):
            series = self._entity(
                "chemical_series",
                str(candidate["chemical_series_id"]),
                namespace="series",
                provenance=ProvenanceSource(source_type="portfolio_candidate", source_id=source_id),
            )
            self._relation(
                entity.entity_id,
                series.entity_id,
                "has_series",
                "source_backed",
                0.7,
                [ProvenanceSource(source_type="portfolio_candidate", source_id=source_id)],
            )
        for risk in candidate.get("blocking_risks") or []:
            risk_entity = self._entity(
                "developability_risk",
                str(risk),
                namespace="risk",
                provenance=ProvenanceSource(source_type="portfolio_candidate", source_id=source_id),
            )
            self._relation(
                entity.entity_id,
                risk_entity.entity_id,
                "blocked_by",
                "source_backed",
                0.8,
                [ProvenanceSource(source_type="portfolio_candidate", source_id=source_id)],
            )
        known_match = (
            candidate.get("metadata", {}).get("known_chemistry_match")
            if isinstance(candidate.get("metadata"), dict)
            else None
        )
        if known_match:
            known = self._entity(
                "molecule",
                str(known_match),
                provenance=ProvenanceSource(source_type="portfolio_candidate", source_id=source_id),
            )
            self._relation(
                entity.entity_id,
                known.entity_id,
                "novel_vs_known",
                "source_backed",
                0.8,
                [ProvenanceSource(source_type="portfolio_candidate", source_id=source_id)],
                metadata={"status": "rediscovered_known_chemistry"},
            )

    def _add_project_program(self, payload: dict[str, Any], source_id: str) -> None:
        project_id = payload.get("project_id") or payload.get("project")
        program_id = payload.get("program_id") or payload.get("program")
        project = None
        if project_id:
            project = self._entity(
                "project",
                str(project_id),
                namespace="project",
                provenance=ProvenanceSource(source_type="artifact", source_id=source_id),
                identifiers={"project_id": str(project_id)},
            )
        if program_id:
            program = self._entity(
                "program",
                str(program_id),
                namespace="program",
                provenance=ProvenanceSource(source_type="artifact", source_id=source_id),
                identifiers={"run_id": str(program_id)},
            )
            if project is not None:
                self._relation(
                    program.entity_id,
                    project.entity_id,
                    "associated_with",
                    "source_backed",
                    0.8,
                    [ProvenanceSource(source_type="artifact", source_id=source_id)],
                    metadata={"source_artifact": source_id},
                )

    def _disease_from_payload(self, payload: dict[str, Any], source_id: str) -> GraphEntity | None:
        disease_record = payload.get("disease")
        if isinstance(disease_record, dict):
            name = _first_text(disease_record, "canonical_name", "name", "input_name")
            identifiers = _dict(disease_record.get("identifiers"))
        else:
            name = str(disease_record) if disease_record else _first_text(payload, "disease_name")
            identifiers = {}
        if not name:
            return None
        return self._entity(
            "disease",
            name,
            provenance=ProvenanceSource(source_type="artifact", source_id=source_id),
            identifiers=identifiers,
        )

    def _target_from_record(self, record: dict[str, Any], source_id: str) -> GraphEntity:
        name = _first_text(record, "symbol", "target_symbol", "name") or "unknown_target"
        identifiers = _dict(record.get("identifiers"))
        namespace = "name" if identifiers else "symbol"
        return self._entity(
            "target",
            name,
            namespace=namespace,
            provenance=ProvenanceSource(source_type="target_artifact", source_id=source_id),
            identifiers=identifiers,
            metadata={"disease_relevance_score": _optional_float(record.get("score"))},
        )

    def _candidate_from_record(self, record: dict[str, Any], source_id: str) -> GraphEntity:
        candidate_name = _first_text(record, "name", "candidate_name", "molecule_name")
        if not candidate_name:
            candidate_name = str(record.get("candidate_id") or "unknown_candidate")
        identifiers = _dict(record.get("identifiers"))
        if record.get("candidate_id"):
            identifiers.setdefault("candidate_id", str(record["candidate_id"]))
        candidate = self._entity(
            "molecule",
            candidate_name,
            provenance=ProvenanceSource(source_type="candidate_artifact", source_id=source_id),
            identifiers=identifiers,
            metadata={
                "score": _optional_float(record.get("score")),
                "origin": record.get("origin") or "existing",
                "direct_evidence_available": record.get("direct_evidence_available"),
            },
        )
        target_names = _string_list(
            record.get("known_targets") or record.get("target_symbols") or record.get("targets")
        )
        if _has_source_backed_target(record):
            for target_name in target_names:
                target = self._entity(
                    "target",
                    target_name,
                    namespace="symbol",
                    provenance=ProvenanceSource(
                        source_type="candidate_artifact", source_id=source_id
                    ),
                )
                self._relation(
                    candidate.entity_id,
                    target.entity_id,
                    "targets",
                    "source_backed",
                    _confidence(record, default=0.6),
                    [ProvenanceSource(source_type="candidate_target", source_id=source_id)],
                    metadata={"candidate_score": _optional_float(record.get("score"))},
                )
        mechanism = _first_text(record, "mechanism_of_action", "mechanism", "mechanism_label")
        if mechanism:
            self._link_mechanism(candidate.entity_id, mechanism, source_id)
        chemical_metadata = record.get("chemical_metadata")
        scaffold = record.get("scaffold_id")
        if isinstance(chemical_metadata, dict):
            scaffold = scaffold or chemical_metadata.get("scaffold_id")
        if scaffold:
            self._link_scaffold(candidate.entity_id, str(scaffold), source_id)
        for evidence in _records(record, "evidence", "evidence_items"):
            self._add_evidence_record(candidate.entity_id, evidence, source_id)
        return candidate

    def _generated_from_record(self, record: dict[str, Any], source_id: str) -> GraphEntity:
        name = _first_text(record, "name", "candidate_name", "molecule_name", "generated_id")
        if not name:
            name = "generated_molecule"
        identifiers = _dict(record.get("identifiers"))
        if record.get("generated_id"):
            identifiers.setdefault("generated_molecule_id", str(record["generated_id"]))
        if record.get("canonical_smiles"):
            identifiers.setdefault("canonical_smiles", str(record["canonical_smiles"]))
        generated = self._entity(
            "generated_molecule",
            name,
            provenance=ProvenanceSource(source_type="generated_artifact", source_id=source_id),
            identifiers=identifiers,
            metadata={
                "origin": "generated",
                "generation_score": _optional_float(
                    record.get("generation_score"), record.get("score")
                ),
                "direct_evidence_available": record.get("direct_evidence_available", False),
            },
        )
        seed_name = _first_text(record, "seed_molecule_name", "seed_name", "parent_molecule_name")
        if seed_name:
            seed = self._entity(
                "molecule",
                seed_name,
                provenance=ProvenanceSource(source_type="generated_artifact", source_id=source_id),
            )
            self._relation(
                generated.entity_id,
                seed.entity_id,
                "generated_from",
                "source_backed",
                0.8,
                [ProvenanceSource(source_type="generated_lineage", source_id=source_id)],
                metadata={"source_artifact": source_id},
            )
        trace_value = record.get("trace")
        trace: dict[str, Any] = trace_value if isinstance(trace_value, dict) else {}
        mechanism = _first_text(
            record, "mechanism", "mechanism_label", "hypothesis_mechanism"
        ) or str(trace.get("hypothesis_mechanism") or "")
        if mechanism:
            self._link_mechanism(
                generated.entity_id,
                mechanism,
                source_id,
                assertion_type="graph_inferred",
            )
        exact_result = bool(
            record.get("exact_imported_result") or record.get("direct_evidence_available")
        )
        if not exact_result:
            self._relation(
                generated.entity_id,
                generated.entity_id,
                "has_no_direct_evidence",
                "graph_inferred",
                1.0,
                [
                    ProvenanceSource(
                        source_type="graph_inference",
                        source_id=f"{source_id}:no_direct_evidence",
                    )
                ],
                metadata={
                    "inferred_relation": True,
                    "not_evidence": True,
                    "reason": "generated molecule without exact imported experimental result",
                },
            )
        known_match = trace.get("known_chemistry_match") or record.get("known_chemistry_match")
        if known_match:
            known = self._entity(
                "molecule",
                str(known_match),
                provenance=ProvenanceSource(source_type="generated_artifact", source_id=source_id),
            )
            self._relation(
                generated.entity_id,
                known.entity_id,
                "novel_vs_known",
                "source_backed",
                0.8,
                [ProvenanceSource(source_type="generated_artifact", source_id=source_id)],
                metadata={"status": "rediscovered_known_chemistry"},
            )
        return generated

    def _experimental_result_from_record(self, record: dict[str, Any], source_id: str) -> None:
        result_id = str(
            record.get("result_id") or record.get("assay_result_id") or f"{source_id}:result"
        )
        candidate_name = _first_text(record, "candidate_name", "molecule_name", "name")
        if not candidate_name:
            return
        assay_name = _first_text(record, "assay_name", "assay", "assay_context_id") or "assay"
        qc_status = str(record.get("qc_status") or "unknown").lower()
        outcome = str(record.get("outcome_label") or record.get("outcome") or "unknown").lower()
        confidence = _confidence(record, default=0.6)
        assay = self._entity(
            "assay",
            assay_name,
            namespace="assay",
            provenance=ProvenanceSource(source_type="experimental_result", source_id=source_id),
            metadata={"target_symbol": record.get("target_symbol")},
        )
        result = self._entity(
            "assay_result",
            result_id,
            namespace="assay_result",
            provenance=ProvenanceSource(source_type="experimental_result", source_id=source_id),
            metadata={
                "candidate_name": candidate_name,
                "target_symbol": record.get("target_symbol"),
                "outcome_label": outcome,
                "qc_status": qc_status,
            },
        )
        candidate = self._entity(
            "molecule",
            candidate_name,
            provenance=ProvenanceSource(source_type="experimental_result", source_id=source_id),
        )
        self._relation(
            candidate.entity_id,
            assay.entity_id,
            "tested_in",
            "source_backed",
            confidence,
            [ProvenanceSource(source_type="experimental_result", source_id=result_id)],
            metadata={"qc_status": qc_status, "source_artifact": source_id},
        )
        self._relation(
            assay.entity_id,
            result.entity_id,
            "produced_result",
            "source_backed",
            confidence,
            [ProvenanceSource(source_type="experimental_result", source_id=result_id)],
            metadata={
                "qc_status": qc_status,
                "outcome_label": outcome,
                "source_artifact": source_id,
            },
        )
        if qc_status == "failed":
            self._relation(
                result.entity_id,
                candidate.entity_id,
                "failed_qc",
                "source_backed",
                confidence,
                [ProvenanceSource(source_type="experimental_result", source_id=result_id)],
                metadata={
                    "qc_status": qc_status,
                    "outcome_label": outcome,
                    "source_artifact": source_id,
                },
            )
            return
        predicate = "supports" if outcome in {"positive", "active", "validated"} else "contradicts"
        self._relation(
            result.entity_id,
            candidate.entity_id,
            predicate,
            "source_backed",
            confidence,
            [ProvenanceSource(source_type="experimental_result", source_id=result_id)],
            metadata={
                "qc_status": qc_status,
                "outcome_label": outcome,
                "target_symbol": record.get("target_symbol"),
                "source_artifact": source_id,
            },
        )

    def _add_evidence_record(self, entity_id: str, record: dict[str, Any], source_id: str) -> None:
        title = _first_text(record, "title", "claim", "summary") or str(
            record.get("source_record_id") or source_id
        )
        paper_id = _first_text(record, "pmid", "doi", "paper_id", "source_record_id")
        paper = None
        if paper_id:
            paper = self._entity(
                "literature_paper",
                paper_id,
                namespace="paper",
                provenance=ProvenanceSource(source_type="literature_artifact", source_id=source_id),
                metadata={"source": record.get("source")},
            )
        claim = self._entity(
            "literature_claim",
            title,
            namespace="claim",
            provenance=ProvenanceSource(source_type="literature_artifact", source_id=source_id),
            metadata={"source": record.get("source"), "evidence_type": record.get("evidence_type")},
        )
        if paper is not None:
            self._relation(
                claim.entity_id,
                paper.entity_id,
                "associated_with",
                "source_backed",
                _confidence(record, default=0.7),
                [ProvenanceSource(source_type="literature_artifact", source_id=source_id)],
            )
        predicate = (
            "contradicts"
            if str(record.get("direction") or "").lower() == "contradictory"
            else "supports"
        )
        self._relation(
            claim.entity_id,
            entity_id,
            predicate,
            "source_backed",
            _confidence(record, default=0.7),
            [ProvenanceSource(source_type="literature_artifact", source_id=source_id)],
            metadata={"source_artifact": source_id},
        )

    def _link_developability_alert(
        self,
        molecule_entity_id: str,
        risk_label: str,
        source_id: str,
        *,
        confidence: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        alert = self._entity(
            "developability_alert",
            risk_label,
            namespace="alert",
            provenance=ProvenanceSource(source_type="developability_artifact", source_id=source_id),
            metadata=metadata,
        )
        self._relation(
            molecule_entity_id,
            alert.entity_id,
            "has_developability_risk",
            "source_backed",
            confidence,
            [ProvenanceSource(source_type="developability_artifact", source_id=source_id)],
            metadata={**(metadata or {}), "does_not_claim_safe_or_unsafe": True},
        )
        legacy_risk = self._entity(
            "developability_risk",
            risk_label,
            namespace="risk",
            provenance=ProvenanceSource(source_type="developability_artifact", source_id=source_id),
            metadata=metadata,
        )
        self._relation(
            molecule_entity_id,
            legacy_risk.entity_id,
            "blocked_by",
            "source_backed",
            confidence,
            [ProvenanceSource(source_type="developability_artifact", source_id=source_id)],
            metadata=metadata or {},
        )

    def _add_developability(
        self,
        molecule_entity_id: str,
        assessment: DevelopabilityAssessment | None,
        source_id: str,
    ) -> None:
        if assessment is None:
            return
        flags = [
            *assessment.admet_property_flags,
            *assessment.toxicity_risk_flags,
            *assessment.medicinal_chemistry_alerts,
            *assessment.chemical_liability_flags,
            *assessment.structure_quality_flags,
        ]
        for flag in flags:
            if flag.severity not in {"medium", "high"}:
                continue
            self._link_developability_alert(
                molecule_entity_id,
                flag.label,
                source_id,
                confidence=0.7 if flag.severity == "medium" else 0.9,
                metadata={"severity": flag.severity, "category": flag.category},
            )
            risk = self._entity(
                "developability_risk",
                flag.label,
                namespace="risk",
                provenance=ProvenanceSource(
                    source_type="developability_assessment", source_id=source_id
                ),
                metadata={"severity": flag.severity, "category": flag.category},
            )
            self._relation(
                molecule_entity_id,
                risk.entity_id,
                "blocked_by",
                "source_backed",
                0.7 if flag.severity == "medium" else 0.9,
                [ProvenanceSource(source_type="developability_assessment", source_id=source_id)],
                metadata={"severity": flag.severity},
            )

    def _add_evidence(self, entity_id: str, evidence: EvidenceItem, source_id: str) -> None:
        claim = self._entity(
            "literature_claim",
            evidence.title,
            namespace="evidence",
            provenance=ProvenanceSource(
                source_type="evidence_item",
                source_id=evidence.source_record_id or source_id,
                citation_ref=evidence.source_record_id,
            ),
            metadata={"source": evidence.source, "evidence_type": evidence.evidence_type},
        )
        self._relation(
            entity_id,
            claim.entity_id,
            "supported_by",
            "source_backed",
            evidence.confidence,
            [
                ProvenanceSource(
                    source_type="evidence_item", source_id=evidence.source_record_id or source_id
                )
            ],
        )

    def _link_mechanism(
        self,
        source_entity_id: str,
        mechanism: str,
        source_id: str,
        *,
        assertion_type: str = "source_backed",
    ) -> None:
        mechanism_entity = self._entity(
            "mechanism",
            mechanism,
            namespace="mechanism",
            provenance=ProvenanceSource(source_type="ranking_run", source_id=source_id),
        )
        self._relation(
            source_entity_id,
            mechanism_entity.entity_id,
            "has_mechanism",
            assertion_type,
            0.7,
            [
                ProvenanceSource(
                    source_type="ranking_run"
                    if assertion_type == "source_backed"
                    else "graph_inference",
                    source_id=source_id,
                )
            ],
        )

    def _link_scaffold(self, source_entity_id: str, scaffold: str, source_id: str) -> None:
        scaffold_entity = self._entity(
            "scaffold",
            scaffold,
            namespace="scaffold",
            provenance=ProvenanceSource(source_type="portfolio_candidate", source_id=source_id),
        )
        self._relation(
            source_entity_id,
            scaffold_entity.entity_id,
            "has_scaffold",
            "source_backed",
            0.7,
            [ProvenanceSource(source_type="portfolio_candidate", source_id=source_id)],
        )

    def _entity(
        self,
        entity_type: str,
        name: str,
        *,
        provenance: ProvenanceSource,
        namespace: str = "name",
        identifiers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GraphEntity:
        identifiers = identifiers or {}
        normalized = dict(normalize_identifier(k, v) for k, v in identifiers.items())
        if namespace == "name" and identifiers:
            system, value = sorted(normalized.items())[0]
            entity_id = make_entity_id(entity_type, system.lower(), value)
        else:
            entity_id = make_entity_id(entity_type, namespace, name)
        existing = self._entities.get(entity_id)
        if existing:
            provenance_ref = (
                provenance.artifact_ref or f"{provenance.source_type}:{provenance.source_id}"
            )
            if provenance_ref not in existing.provenance_refs:
                existing.provenance_refs.append(provenance_ref)
            if (
                provenance.artifact_ref
                and provenance.artifact_ref not in existing.source_artifact_ids
            ):
                existing.source_artifact_ids.append(provenance.artifact_ref)
            for key, value in (metadata or {}).items():
                if value is not None:
                    existing.metadata.setdefault(key, value)
            return existing
        entity = GraphEntity(
            entity_id=entity_id,
            entity_type=entity_type,  # type: ignore[arg-type]
            name=name,
            identifiers=normalized,
            source_artifact_ids=[provenance.artifact_ref] if provenance.artifact_ref else [],
            provenance_refs=[
                provenance.artifact_ref or f"{provenance.source_type}:{provenance.source_id}"
            ],
            metadata={key: value for key, value in (metadata or {}).items() if value is not None},
        )
        self._entities[entity_id] = entity
        return entity

    def _relation(
        self,
        source: str,
        target: str,
        relation_type: str,
        assertion_type: str,
        confidence: float,
        provenance: list[ProvenanceSource],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        source_artifact_ids = [
            source.artifact_ref or f"{source.source_type}:{source.source_id}"
            for source in provenance
        ]
        source_record_ids = [source.source_id for source in provenance]
        relation = GraphRelation(
            subject_entity_id=source,
            predicate=relation_type,
            object_entity_id=target,
            relation_type=(
                "inferred" if assertion_type == "graph_inferred" else _relation_kind(relation_type)
            ),
            confidence=max(0.0, min(float(confidence), 1.0)),
            direction=_relation_direction(relation_type),
            source_artifact_ids=source_artifact_ids,
            source_record_ids=source_record_ids,
            metadata=metadata or {},
        )
        self._relations.setdefault(relation.relation_id, relation)


def _run_id(run: RankingRun, fallback: str) -> str:
    for trace in run.traces:
        if trace.metadata.get("run_id"):
            return str(trace.metadata["run_id"])
    return str(run.disease.identifiers.get("run_id") or fallback)


def _relation_kind(predicate: str) -> str:
    if predicate in {
        "validated_by",
        "contradicted_by",
        "tested_in",
        "produced_result",
        "failed_qc",
        "supports",
        "contradicts",
    }:
        return "experimental"
    if predicate in {"supported_by", "associated_with"}:
        return "evidence_backed"
    if predicate == "reviewed_as":
        return "review"
    if predicate == "generated_from":
        return "generated_lineage"
    if predicate in {"same_as", "similar_to"}:
        return "ontology_mapping"
    if predicate == "predicted_by_model":
        return "model_prediction"
    return "computational"


def _relation_direction(predicate: str) -> str:
    if predicate in {"contradicted_by", "contradicts", "failed_qc"}:
        return "contradictory"
    if predicate in {"blocked_by", "has_developability_risk"}:
        return "risk"
    if predicate in {
        "associated_with",
        "targets",
        "modulates",
        "has_mechanism",
        "has_scaffold",
        "tested_in",
        "produced_result",
        "supports",
        "supported_by",
        "validated_by",
        "generated_from",
        "reviewed_as",
        "selected_in_portfolio",
        "predicted_by_model",
    }:
        return "supportive"
    return "neutral"


def _records(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [item for item in value.values() if isinstance(item, dict)]
    return []


def _first_text(record: dict[str, Any] | None, *keys: str) -> str | None:
    if not record:
        return None
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _optional_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _confidence(record: dict[str, Any], *, default: float) -> float:
    value = _optional_float(
        record.get("confidence"),
        record.get("score"),
        record.get("prediction_score"),
        record.get("priority_score"),
    )
    if value is None:
        return default
    return max(0.0, min(value, 1.0))


def _has_source_backed_target(record: dict[str, Any]) -> bool:
    if record.get("target_source_backed") is True or record.get("evidence_backed") is True:
        return True
    if record.get("direct_evidence_available") is False:
        return False
    origin = str(record.get("origin") or record.get("candidate_origin") or "").lower()
    if origin == "generated":
        return False
    return bool(
        record.get("known_targets") or record.get("target_symbols") or record.get("targets")
    )


def _risk_labels(record: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in (
        "blocking_risks",
        "risk_flags",
        "developability_risks",
        "medicinal_chemistry_alerts",
        "toxicity_risk_flags",
        "chemical_liability_flags",
        "admet_property_flags",
    ):
        value = record.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    label = _first_text(item, "label", "name", "risk", "description")
                    if label:
                        labels.append(label)
                elif item is not None:
                    labels.append(str(item))
        elif isinstance(value, dict):
            label = _first_text(value, "label", "name", "risk", "description")
            if label:
                labels.append(label)
        elif isinstance(value, str):
            labels.append(value)
    return sorted(set(labels))
