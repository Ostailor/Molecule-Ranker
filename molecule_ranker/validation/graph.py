from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.knowledge_graph.builder import GraphBuilder
from molecule_ranker.knowledge_graph.contradiction import (
    build_contradiction_report,
    build_staleness_report,
)
from molecule_ranker.knowledge_graph.dashboard import write_knowledge_graph_dashboard
from molecule_ranker.knowledge_graph.export import export_graph_turtle
from molecule_ranker.knowledge_graph.mechanism import extract_mechanism_hypotheses
from molecule_ranker.knowledge_graph.recommendations import generate_graph_recommendations
from molecule_ranker.knowledge_graph.schemas import GraphRelation, KnowledgeGraph
from molecule_ranker.knowledge_graph.validation import validate_knowledge_graph
from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

GraphValidationStatus = Literal["pass", "fail"]
GraphValidationFixture = Literal["golden", "fake_relation", "overclaim", "causality_claim"]

GRAPH_VALIDATION_STEPS = [
    "synthetic artifacts built for two projects",
    "knowledge graph built",
    "entities deduplicated",
    "mechanisms extracted",
    "contradictions detected",
    "stale decisions detected",
    "graph recommendations generated",
    "RDF/Turtle exported",
    "graph dashboard generated",
    "graph guardrails verified",
]

GRAPH_GUARDRAIL_CATEGORIES = (
    "Graph inference boundary",
    "Codex output boundary",
    "Graph grounding",
    "Generated molecule claims",
    "Causality claims",
    "Model prediction boundary",
    "Review decision boundary",
    "Protocol boundary",
)

IGNORED_FILENAMES = {
    "graph_guardrail_audit.json",
    "graph_guardrail_audit.md",
    "graph_validation_report.json",
    "graph_validation_report.md",
}

PROTOCOL_PATTERN = re.compile(
    r"\b("
    r"synthesis|synthetic\s+route|reaction\s+scheme|reagent|reagents|incubat|pipette|"
    r"centrifuge|wash\s+buffer|dose|dosing|mg/kg|administer|patient\s+guidance|"
    r"lab\s+protocol|step-by-step\s+protocol"
    r")\b",
    re.IGNORECASE,
)
GENERATED_OVERCLAIM_PATTERN = re.compile(
    r"\bgenerated\s+(?:molecule|candidate|compound)[^.\n]{0,120}\b"
    r"(?:is|are|was|were|will\s+be|has\s+been)\s+"
    r"(?:active|safe|effective|validated|proven|binding|a\s+cure)\b",
    re.IGNORECASE,
)
CAUSALITY_PATTERN = re.compile(
    r"\bgraph\s+path[^.\n]{0,100}\b(?:proves?|proof\s+of)\s+caus",
    re.IGNORECASE,
)
MODEL_EVIDENCE_PATTERN = re.compile(
    r"\bmodel\s+prediction[^.\n]{0,80}\b(?:is|as|counts\s+as)\s+"
    r"(?:evidence|experimental\s+evidence|biomedical\s+evidence)\b",
    re.IGNORECASE,
)
REVIEW_EVIDENCE_PATTERN = re.compile(
    r"\breview\s+decision[^.\n]{0,100}\b(?:is|as|counts\s+as)\s+"
    r"(?:biomedical\s+evidence|experimental\s+evidence|evidence)\b",
    re.IGNORECASE,
)
CODEX_EVIDENCE_PATTERN = re.compile(
    r"\b(?:codex|assistant)\s+(?:graph\s+)?summary[^.\n]{0,100}\b"
    r"(?:is|as|counts\s+as)\s+(?:evidence|biomedical\s+truth)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GraphGuardrailFinding:
    category: str
    check_id: str
    severity: str
    artifact_path: str
    message: str
    excerpt: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "check_id": self.check_id,
            "severity": self.severity,
            "artifact_path": self.artifact_path,
            "message": self.message,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class GraphGuardrailAuditReport:
    status: GraphValidationStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[GraphGuardrailFinding]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root_dir": str(self.root_dir),
            "artifact_count": self.artifact_count,
            "categories": list(self.categories),
            "finding_count": len(self.findings),
            "findings": [finding.as_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class GraphValidationReport:
    status: GraphValidationStatus
    output_dir: Path
    fixture: str
    artifacts: list[str]
    required_steps: list[str]
    entity_count: int
    relation_count: int
    mechanism_count: int
    contradiction_count: int
    stale_relation_count: int
    recommendation_count: int
    guardrail_audit: GraphGuardrailAuditReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "fixture": self.fixture,
            "artifacts": self.artifacts,
            "required_steps": self.required_steps,
            "entity_count": self.entity_count,
            "relation_count": self.relation_count,
            "mechanism_count": self.mechanism_count,
            "contradiction_count": self.contradiction_count,
            "stale_relation_count": self.stale_relation_count,
            "recommendation_count": self.recommendation_count,
            "guardrail_audit": self.guardrail_audit.as_dict(),
        }


@dataclass(frozen=True)
class _ArtifactSnapshot:
    path: Path
    relative_path: str
    text: str
    json_payload: Any | None

    @property
    def is_codex_output(self) -> bool:
        name = self.relative_path.lower()
        return "codex" in name or "assistant" in name


def run_graph_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/graph",
    fixture: GraphValidationFixture = "golden",
) -> GraphValidationReport:
    """Run the deterministic V1.5 knowledge graph validation workflow."""

    resolved_output = Path(output_dir).resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    graph = _write_graph_validation_workflow(resolved_output, fixture=fixture)
    audit = run_graph_guardrail_audit(resolved_output)
    artifacts = sorted(
        str(path.relative_to(resolved_output))
        for path in resolved_output.rglob("*")
        if path.is_file()
    )
    contradiction_payload = json.loads(
        (resolved_output / "contradiction_report.json").read_text(encoding="utf-8")
    )
    staleness_payload = json.loads(
        (resolved_output / "staleness_report.json").read_text(encoding="utf-8")
    )
    recommendation_payload = json.loads(
        (resolved_output / "graph_recommendations.json").read_text(encoding="utf-8")
    )
    status: GraphValidationStatus = "pass" if audit.status == "pass" else "fail"
    report = GraphValidationReport(
        status=status,
        output_dir=resolved_output,
        fixture=fixture,
        artifacts=artifacts,
        required_steps=GRAPH_VALIDATION_STEPS,
        entity_count=len(graph.entities),
        relation_count=len(graph.relations),
        mechanism_count=len(graph.mechanisms),
        contradiction_count=len(contradiction_payload.get("contradiction_relations", [])),
        stale_relation_count=len(staleness_payload.get("stale_relations", [])),
        recommendation_count=len(recommendation_payload.get("recommendations", [])),
        guardrail_audit=audit,
    )
    write_json_artifact(resolved_output / "graph_validation_report.json", report.as_dict())
    write_markdown_artifact(
        resolved_output / "graph_validation_report.md",
        "V1.5 Knowledge Graph Validation Report",
        [
            f"- Status: `{report.status}`",
            f"- Fixture: `{fixture}`",
            f"- Entities: {report.entity_count}",
            f"- Relations: {report.relation_count}",
            f"- Mechanisms: {report.mechanism_count}",
            f"- Contradictions: {report.contradiction_count}",
            f"- Stale relations: {report.stale_relation_count}",
            f"- Recommendations: {report.recommendation_count}",
            f"- Guardrail findings: {len(audit.findings)}",
            "",
            "## Required Steps",
            *[f"- {step}" for step in report.required_steps],
        ],
    )
    return report


def run_graph_guardrail_audit(path: str | Path) -> GraphGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = _load_artifacts(root)
    graph = _load_graph(root)
    findings: list[GraphGuardrailFinding] = []

    if graph is not None:
        findings.extend(_schema_validation_findings(graph, root))
        findings.extend(_graph_boundary_findings(graph, root))
        known_entity_ids = {entity.entity_id for entity in graph.entities}
        known_relation_ids = {relation.relation_id for relation in graph.relations}
    else:
        known_entity_ids = set()
        known_relation_ids = set()
        findings.append(
            GraphGuardrailFinding(
                category="Graph grounding",
                check_id="missing_graph",
                severity="high",
                artifact_path=str(root),
                message="knowledge_graph.json was not produced.",
            )
        )

    for artifact in artifacts:
        findings.extend(_text_guardrail_findings(artifact))
        if artifact.json_payload is not None:
            findings.extend(
                _json_guardrail_findings(
                    artifact,
                    known_entity_ids=known_entity_ids,
                    known_relation_ids=known_relation_ids,
                )
            )
        if artifact.is_codex_output:
            findings.extend(
                _codex_grounding_findings(
                    artifact,
                    known_entity_ids=known_entity_ids,
                    known_relation_ids=known_relation_ids,
                )
            )

    report = GraphGuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=GRAPH_GUARDRAIL_CATEGORIES,
        findings=_dedupe_findings(findings),
    )
    _write_graph_guardrail_audit_reports(report)
    return report


def _write_graph_validation_workflow(
    output_dir: Path,
    *,
    fixture: GraphValidationFixture,
) -> KnowledgeGraph:
    project_a = output_dir / "project-a"
    project_b = output_dir / "project-b"
    _write_project_artifacts(project_a, project_id="project-a", outcome="positive")
    _write_project_artifacts(project_b, project_id="project-b", outcome="negative")

    graphs = [
        GraphBuilder().build_from_directory(project_a, graph_id="kg-validation-project-a"),
        GraphBuilder().build_from_directory(project_b, graph_id="kg-validation-project-b"),
    ]
    graph = _dedupe_graphs(graphs)
    _force_temporal_order_for_staleness(graph)
    graph.mechanisms = extract_mechanism_hypotheses(graph)
    contradiction_report = build_contradiction_report(graph)
    graph.relations = _merge_relations(
        graph.relations,
        contradiction_report.contradiction_relations,
    )
    staleness_report = build_staleness_report(graph)
    graph.relations = _merge_relations(graph.relations, staleness_report.stale_relations)
    recommendations = generate_graph_recommendations(graph, current_project_id="project-a")

    write_json_artifact(output_dir / "knowledge_graph.json", graph.model_dump(mode="json"))
    write_json_artifact(
        output_dir / "mechanism_hypotheses.json",
        {"mechanisms": [item.model_dump(mode="json") for item in graph.mechanisms]},
    )
    write_json_artifact(output_dir / "contradiction_report.json", _jsonable(contradiction_report))
    write_json_artifact(output_dir / "staleness_report.json", _jsonable(staleness_report))
    write_json_artifact(
        output_dir / "graph_recommendations.json",
        {
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "advisory": True,
            "automatic_decisions_disabled": True,
        },
    )
    export_graph_turtle(graph, output_dir / "knowledge_graph.ttl")
    write_knowledge_graph_dashboard(graph, output_dir / "dashboard")
    _write_codex_graph_summary(output_dir / "codex_graph_summary.json", graph, fixture=fixture)
    _write_graph_review_note(output_dir / "graph_review_note.md", fixture=fixture)
    return graph


def _write_project_artifacts(path: Path, *, project_id: str, outcome: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    write_json_artifact(
        path / "candidates.json",
        {
            "project_id": project_id,
            "program_id": "program-parkinson",
            "disease": {"canonical_name": "Parkinson disease", "id": "EFO:0002508"},
            "targets": [
                {
                    "symbol": "MAOB",
                    "evidence": [
                        {
                            "source": "opentargets",
                            "source_id": f"OTAR-{project_id}",
                            "title": "MAOB association with Parkinson disease",
                            "confidence": 0.82,
                        }
                    ],
                }
            ],
            "candidates": [
                {
                    "candidate_id": "candidate-rasagiline",
                    "name": "Rasagiline",
                    "known_targets": ["MAOB"],
                    "target_source": "chembl",
                    "score": 0.81,
                    "scaffold": "indane",
                    "mechanism": "MAOB modulation in Parkinson disease",
                    "evidence": [
                        {
                            "source": "pubmed",
                            "pmid": "12345678",
                            "claim": "MAOB inhibition is discussed for Parkinson disease.",
                            "direction": "supportive",
                            "confidence": 0.72,
                        }
                    ],
                }
            ],
        },
    )
    write_json_artifact(
        path / "generated_candidates.json",
        {
            "generated_candidates": [
                {
                    "generated_id": f"gen-{project_id}-1",
                    "name": "Generated MAOB analogue",
                    "canonical_smiles": "COc1ccc(CCN)cc1",
                    "seed_molecule_name": "Rasagiline",
                    "generation_score": 0.78,
                    "mechanism": "Generated MAOB modulation hypothesis",
                    "direct_evidence_available": False,
                    "known_chemistry_match": "Known MAOB analogue",
                }
            ]
        },
    )
    write_json_artifact(
        path / "experimental_results.json",
        {
            "results": [
                {
                    "result_id": f"assay-{project_id}-{outcome}",
                    "candidate_name": "Rasagiline",
                    "target_symbol": "MAOB",
                    "assay_name": "MAOB functional assay",
                    "endpoint": "functional_activity",
                    "outcome_label": outcome,
                    "qc_status": "passed",
                    "confidence": 0.88 if outcome == "positive" else 0.9,
                }
            ]
        },
    )
    write_json_artifact(
        path / "model_predictions.json",
        {
            "predictions": [
                {
                    "prediction_id": f"pred-{project_id}-rasagiline",
                    "candidate_name": "Rasagiline",
                    "target_symbol": "MAOB",
                    "model_name": "synthetic_surrogate",
                    "prediction_score": 0.91,
                    "confidence": 0.91,
                    "trained_at": (datetime.now(UTC) - timedelta(days=90)).isoformat(),
                }
            ]
        },
    )
    write_json_artifact(
        path / "developability.json",
        {
            "assessments": [
                {
                    "candidate_name": "Rasagiline",
                    "risk_level": "critical" if outcome == "negative" else "low",
                    "risk_flags": (
                        ["critical_safety_liability"] if outcome == "negative" else ["monitor"]
                    ),
                    "developability_score": 0.2 if outcome == "negative" else 0.74,
                }
            ]
        },
    )
    write_json_artifact(
        path / "review_queue.json",
        {
            "review_items": [
                {
                    "review_item_id": f"review-item-{project_id}",
                    "candidate_id": "candidate-rasagiline",
                    "candidate_name": "Rasagiline",
                }
            ],
            "decisions": [
                {
                    "decision_id": f"review-decision-{project_id}",
                    "review_item_id": f"review-item-{project_id}",
                    "candidate_name": "Rasagiline",
                    "decision": "accepted_for_followup",
                    "confidence": 0.76,
                }
            ],
        },
    )
    write_json_artifact(
        path / "portfolio_optimization.json",
        {
            "portfolio_id": f"portfolio-{project_id}",
            "selected_candidates": [
                {
                    "candidate_name": "Rasagiline",
                    "portfolio_id": f"portfolio-{project_id}",
                    "selection_reason": "synthetic validation fixture",
                }
            ],
        },
    )


def _dedupe_graphs(graphs: list[KnowledgeGraph]) -> KnowledgeGraph:
    entities = {entity.entity_id: entity for graph in graphs for entity in graph.entities}
    relations = {relation.relation_id: relation for graph in graphs for relation in graph.relations}
    return KnowledgeGraph(
        graph_id="kg-validation-v1-5",
        entities=sorted(entities.values(), key=lambda entity: entity.entity_id),
        relations=sorted(relations.values(), key=lambda relation: relation.relation_id),
        metadata={"validation": "v1.5", "projects": ["project-a", "project-b"]},
    )


def _force_temporal_order_for_staleness(graph: KnowledgeGraph) -> None:
    old = datetime.now(UTC) - timedelta(days=45)
    new = datetime.now(UTC)
    for relation in graph.relations:
        if relation.predicate == "reviewed_as":
            relation.created_at = old
            relation.updated_at = old
        if relation.relation_type == "experimental":
            relation.created_at = new
            relation.updated_at = new


def _merge_relations(
    existing: list[GraphRelation],
    additions: list[GraphRelation],
) -> list[GraphRelation]:
    merged = {relation.relation_id: relation for relation in existing}
    for relation in additions:
        merged[relation.relation_id] = relation
    return sorted(merged.values(), key=lambda relation: relation.relation_id)


def _write_codex_graph_summary(
    path: Path,
    graph: KnowledgeGraph,
    *,
    fixture: GraphValidationFixture,
) -> None:
    entity_ids = [entity.entity_id for entity in graph.entities[:3]]
    relation_ids = [relation.relation_id for relation in graph.relations[:3]]
    payload: dict[str, Any] = {
        "assistant_output": True,
        "not_evidence": True,
        "summary": (
            "Codex graph summary describes graph-linked hypotheses only. It is not evidence, "
            "not an assay result, and not a causal, activity, safety, or binding claim."
        ),
        "entity_ids": entity_ids,
        "relation_ids": relation_ids,
        "provenance_ids": ["generated_artifact:codex_graph_summary"],
        "artifact_ids": ["knowledge_graph.json", "contradiction_report.json"],
    }
    if fixture == "fake_relation":
        payload["relation_ids"] = [*relation_ids, "rel:invented-validation-fake"]
        payload["summary"] = "Codex graph summary cites a relation not present in the graph."
    write_json_artifact(path, payload)


def _write_graph_review_note(path: Path, *, fixture: GraphValidationFixture) -> None:
    lines = [
        "The graph is a memory and reasoning layer for reviewing prior program context.",
        "Generated molecules are computational hypotheses unless direct source records "
        "support them.",
        "Model predictions are predictions, not evidence.",
        "Review decisions are workflow records, not biomedical evidence.",
        "Graph paths are hypotheses and cannot establish causality, efficacy, safety, "
        "binding, or activity.",
    ]
    if fixture == "overclaim":
        lines.append("The generated molecule is active and safe in MAOB follow-up.")
        lines.append("The model prediction is evidence for activity.")
        lines.append("The review decision is biomedical evidence.")
        lines.append("Use a step-by-step lab protocol with dosing at 10 mg/kg.")
    if fixture == "causality_claim":
        lines.append("The graph path proves causality for Parkinson disease.")
    write_markdown_artifact(path, "Graph Review Note", lines)


def _load_graph(root: Path) -> KnowledgeGraph | None:
    path = root / "knowledge_graph.json"
    if not path.exists():
        return None
    return KnowledgeGraph.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _load_artifacts(root: Path) -> list[_ArtifactSnapshot]:
    artifacts: list[_ArtifactSnapshot] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in IGNORED_FILENAMES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        json_payload = None
        if path.suffix.lower() == ".json":
            try:
                json_payload = json.loads(text)
            except json.JSONDecodeError:
                json_payload = None
        artifacts.append(
            _ArtifactSnapshot(
                path=path,
                relative_path=str(path.relative_to(root)),
                text=text,
                json_payload=json_payload,
            )
        )
    return artifacts


def _schema_validation_findings(
    graph: KnowledgeGraph,
    root: Path,
) -> list[GraphGuardrailFinding]:
    report = validate_knowledge_graph(graph)
    advisory_relation_ids = {
        relation.relation_id
        for relation in graph.relations
        if relation.predicate in {"contradicts", "stale_due_to"} and relation.metadata.get("reason")
    }
    findings: list[GraphGuardrailFinding] = []
    for error in report.errors:
        relation_id = error.split(":", maxsplit=2)
        if len(relation_id) >= 2:
            possible_id = f"{relation_id[0]}:{relation_id[1]}"
            if possible_id in advisory_relation_ids:
                continue
        findings.append(
            GraphGuardrailFinding(
                category="Graph inference boundary",
                check_id="graph_schema_validation",
                severity="high",
                artifact_path=str(root / "knowledge_graph.json"),
                message=error,
            )
        )
    return findings


def _graph_boundary_findings(
    graph: KnowledgeGraph,
    root: Path,
) -> list[GraphGuardrailFinding]:
    findings: list[GraphGuardrailFinding] = []
    for relation in graph.relations:
        metadata_text = json.dumps(_jsonable(relation.metadata), sort_keys=True)
        if relation.relation_type == "inferred" and (
            relation.evidence_item_ids
            or "EvidenceItem" in metadata_text
            or "AssayResult" in metadata_text
        ):
            findings.append(
                GraphGuardrailFinding(
                    category="Graph inference boundary",
                    check_id="inferred_relation_not_evidence",
                    severity="high",
                    artifact_path=str(root / "knowledge_graph.json"),
                    message=(
                        f"{relation.relation_id} is inferred but is labeled like evidence "
                        "or an assay result."
                    ),
                )
            )
        if relation.relation_type == "model_prediction":
            metadata = relation.metadata
            if metadata.get("evidence") is True or metadata.get("is_evidence") is True:
                findings.append(
                    GraphGuardrailFinding(
                        category="Model prediction boundary",
                        check_id="model_prediction_not_evidence",
                        severity="high",
                        artifact_path=str(root / "knowledge_graph.json"),
                        message=f"{relation.relation_id} labels a model prediction as evidence.",
                    )
                )
    for entity in graph.entities:
        if entity.entity_type == "generated_molecule":
            metadata_text = json.dumps(_jsonable(entity.metadata), sort_keys=True).lower()
            if "safe" in metadata_text or "active" in metadata_text or "proven" in metadata_text:
                findings.append(
                    GraphGuardrailFinding(
                        category="Generated molecule claims",
                        check_id="generated_molecule_overclaim",
                        severity="high",
                        artifact_path=str(root / "knowledge_graph.json"),
                        message=f"{entity.entity_id} contains generated molecule overclaim text.",
                    )
                )
    return findings


def _text_guardrail_findings(artifact: _ArtifactSnapshot) -> list[GraphGuardrailFinding]:
    checks = [
        (
            GENERATED_OVERCLAIM_PATTERN,
            "Generated molecule claims",
            "generated_molecule_overclaim",
            "Generated molecules must remain computational hypotheses.",
        ),
        (
            CAUSALITY_PATTERN,
            "Causality claims",
            "graph_path_causality_overclaim",
            "Graph paths must not be called proof of causality.",
        ),
        (
            MODEL_EVIDENCE_PATTERN,
            "Model prediction boundary",
            "model_prediction_called_evidence",
            "Model predictions must not be called evidence.",
        ),
        (
            REVIEW_EVIDENCE_PATTERN,
            "Review decision boundary",
            "review_decision_called_evidence",
            "Review decisions must not be called biomedical evidence.",
        ),
        (
            CODEX_EVIDENCE_PATTERN,
            "Codex output boundary",
            "codex_summary_called_evidence",
            "Codex graph summaries must be assistant output, not evidence.",
        ),
        (
            PROTOCOL_PATTERN,
            "Protocol boundary",
            "synthesis_lab_or_dosing_text",
            "Graph validation artifacts must not include synthesis, lab, dosing, or "
            "patient guidance.",
        ),
    ]
    findings: list[GraphGuardrailFinding] = []
    for pattern, category, check_id, message in checks:
        match = pattern.search(artifact.text)
        if match is None:
            continue
        excerpt = _excerpt(artifact.text, match.start(), match.end())
        if _is_boundary_disclaimer(excerpt):
            continue
        findings.append(
            GraphGuardrailFinding(
                category=category,
                check_id=check_id,
                severity="high",
                artifact_path=str(artifact.path),
                message=message,
                excerpt=excerpt,
            )
        )
    return findings


def _json_guardrail_findings(
    artifact: _ArtifactSnapshot,
    *,
    known_entity_ids: set[str],
    known_relation_ids: set[str],
) -> list[GraphGuardrailFinding]:
    payload = artifact.json_payload
    if not isinstance(payload, dict):
        return []
    findings: list[GraphGuardrailFinding] = []
    if payload.get("assistant_output") is True and payload.get("is_evidence") is True:
        findings.append(
            GraphGuardrailFinding(
                category="Codex output boundary",
                check_id="codex_summary_called_evidence",
                severity="high",
                artifact_path=str(artifact.path),
                message="Codex graph summary JSON labels assistant output as evidence.",
            )
        )
    for key, known, category in (
        ("entity_ids", known_entity_ids, "Graph grounding"),
        ("relation_ids", known_relation_ids, "Graph grounding"),
    ):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value)
            if text not in known:
                findings.append(
                    GraphGuardrailFinding(
                        category=category,
                        check_id="invented_graph_reference",
                        severity="high",
                        artifact_path=str(artifact.path),
                        message=f"{key} contains unknown graph reference {text}.",
                    )
                )
    return findings


def _codex_grounding_findings(
    artifact: _ArtifactSnapshot,
    *,
    known_entity_ids: set[str],
    known_relation_ids: set[str],
) -> list[GraphGuardrailFinding]:
    if not isinstance(artifact.json_payload, dict):
        return []
    payload = artifact.json_payload
    findings: list[GraphGuardrailFinding] = []
    for required_key in ("entity_ids", "relation_ids", "provenance_ids", "artifact_ids"):
        if not payload.get(required_key):
            findings.append(
                GraphGuardrailFinding(
                    category="Codex output boundary",
                    check_id="codex_summary_missing_citations",
                    severity="high",
                    artifact_path=str(artifact.path),
                    message=f"Codex graph output is missing {required_key}.",
                )
            )
    findings.extend(
        _json_guardrail_findings(
            artifact,
            known_entity_ids=known_entity_ids,
            known_relation_ids=known_relation_ids,
        )
    )
    return findings


def _write_graph_guardrail_audit_reports(report: GraphGuardrailAuditReport) -> None:
    write_json_artifact(report.root_dir / "graph_guardrail_audit.json", report.as_dict())
    write_markdown_artifact(
        report.root_dir / "graph_guardrail_audit.md",
        "V1.5 Graph Guardrail Audit",
        [
            f"- Status: `{report.status}`",
            f"- Artifacts: {report.artifact_count}",
            f"- Findings: {len(report.findings)}",
            "",
            "## Findings",
            *[
                f"- `{finding.check_id}` ({finding.severity}) {finding.artifact_path}: "
                f"{finding.message}"
                for finding in report.findings
            ],
        ],
    )


def _dedupe_findings(findings: list[GraphGuardrailFinding]) -> list[GraphGuardrailFinding]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[GraphGuardrailFinding] = []
    for finding in findings:
        key = (
            finding.category,
            finding.check_id,
            finding.artifact_path,
            finding.message,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _excerpt(text: str, start: int, end: int) -> str:
    return " ".join(text[max(0, start - 60) : min(len(text), end + 60)].split())


def _is_boundary_disclaimer(excerpt: str) -> bool:
    lowered = excerpt.lower()
    return any(
        phrase in lowered
        for phrase in (
            "do not prove",
            "does not prove",
            "not proof",
            "not a causal",
            "no medical advice",
            "no synthesis",
            "no wet-lab",
            "no lab",
            "not include synthesis",
            "must not include synthesis",
        )
    )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return value
