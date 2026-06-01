from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.hypotheses.evidence_gap import analyze_evidence_gaps_for_hypotheses
from molecule_ranker.hypotheses.falsification import build_falsification_criteria
from molecule_ranker.hypotheses.generator import generate_hypothesis_candidates
from molecule_ranker.hypotheses.questions import plan_research_questions
from molecule_ranker.hypotheses.ranking import rank_research_hypotheses
from molecule_ranker.hypotheses.reports import render_hypothesis_report_markdown
from molecule_ranker.hypotheses.review import HypothesisReviewService
from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    HypothesisGenerationRun,
    ResearchHypothesis,
    TestableResearchQuestion,
)
from molecule_ranker.hypotheses.store import HypothesisStore
from molecule_ranker.hypotheses.validation import (
    detect_hypothesis_guardrail_violations,
    observed_hypothesis_references,
)
from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    KnowledgeGraph,
)
from molecule_ranker.knowledge_graph.store import KnowledgeGraphStore
from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

HypothesisValidationStatus = Literal["pass", "fail"]
HypothesisValidationFixture = Literal[
    "golden",
    "invented_relation",
    "protocol_text",
    "generated_activity_claim",
]

HYPOTHESIS_VALIDATION_STEPS = [
    "synthetic graph built",
    "hypotheses generated",
    "evidence gaps generated",
    "falsification criteria generated",
    "research questions generated",
    "hypotheses ranked",
    "one hypothesis reviewed",
    "hypothesis report generated",
    "hypothesis guardrails verified",
]

HYPOTHESIS_GUARDRAIL_CATEGORIES = (
    "Hypothesis evidence boundary",
    "Graph inference boundary",
    "Codex grounding",
    "Generated molecule boundary",
    "Research question boundary",
    "Protocol boundary",
    "Synthesis boundary",
    "Dosing boundary",
    "Medical advice boundary",
    "Unsupported claim boundary",
)

IGNORED_FILENAMES = {
    "hypothesis_guardrail_audit.json",
    "hypothesis_guardrail_audit.md",
    "hypothesis_validation_report.json",
    "hypothesis_validation_report.md",
}


@dataclass(frozen=True)
class HypothesisGuardrailFinding:
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
class HypothesisGuardrailAuditReport:
    status: HypothesisValidationStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[HypothesisGuardrailFinding]

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
class HypothesisValidationReport:
    status: HypothesisValidationStatus
    output_dir: Path
    fixture: str
    artifacts: list[str]
    required_steps: list[str]
    hypothesis_count: int
    generated_molecule_count: int
    evidence_gap_count: int
    falsification_criterion_count: int
    research_question_count: int
    lifecycle_event_count: int
    guardrail_audit: HypothesisGuardrailAuditReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "fixture": self.fixture,
            "artifacts": self.artifacts,
            "required_steps": self.required_steps,
            "hypothesis_count": self.hypothesis_count,
            "generated_molecule_count": self.generated_molecule_count,
            "evidence_gap_count": self.evidence_gap_count,
            "falsification_criterion_count": self.falsification_criterion_count,
            "research_question_count": self.research_question_count,
            "lifecycle_event_count": self.lifecycle_event_count,
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
        return "codex" in name or (
            isinstance(self.json_payload, dict)
            and bool(self.json_payload.get("assistant_output"))
        )


def run_hypothesis_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/hypotheses",
    fixture: HypothesisValidationFixture = "golden",
) -> HypothesisValidationReport:
    """Run the deterministic V1.6 hypothesis validation workflow."""

    resolved_output = Path(output_dir).resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    workflow = _write_hypothesis_validation_workflow(resolved_output, fixture=fixture)
    audit = run_hypothesis_guardrail_audit(resolved_output)
    artifacts = sorted(
        str(path.relative_to(resolved_output))
        for path in resolved_output.rglob("*")
        if path.is_file()
    )
    status: HypothesisValidationStatus = "pass" if audit.status == "pass" else "fail"
    report = HypothesisValidationReport(
        status=status,
        output_dir=resolved_output,
        fixture=fixture,
        artifacts=artifacts,
        required_steps=HYPOTHESIS_VALIDATION_STEPS,
        hypothesis_count=len(workflow["hypotheses"]),
        generated_molecule_count=sum(
            1
            for hypothesis in workflow["hypotheses"]
            if hypothesis.hypothesis_type == "generated_molecule"
        ),
        evidence_gap_count=sum(len(items) for items in workflow["gaps"].values()),
        falsification_criterion_count=sum(len(items) for items in workflow["criteria"].values()),
        research_question_count=sum(len(items) for items in workflow["questions"].values()),
        lifecycle_event_count=len(workflow["lifecycle_events"]),
        guardrail_audit=audit,
    )
    write_json_artifact(resolved_output / "hypothesis_validation_report.json", report.as_dict())
    write_markdown_artifact(
        resolved_output / "hypothesis_validation_report.md",
        "V1.6 Hypothesis Validation Report",
        [
            f"- Status: `{report.status}`",
            f"- Fixture: `{fixture}`",
            f"- Hypotheses: {report.hypothesis_count}",
            f"- Generated-molecule hypotheses: {report.generated_molecule_count}",
            f"- Evidence gaps: {report.evidence_gap_count}",
            f"- Falsification criteria: {report.falsification_criterion_count}",
            f"- Research questions: {report.research_question_count}",
            f"- Lifecycle events: {report.lifecycle_event_count}",
            f"- Guardrail findings: {len(audit.findings)}",
            "",
            "## Required Steps",
            *[f"- {step}" for step in report.required_steps],
        ],
    )
    return report


def run_hypothesis_guardrail_audit(path: str | Path) -> HypothesisGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = _load_artifacts(root)
    graph = _load_graph(root)
    findings: list[HypothesisGuardrailFinding] = []

    if graph is None:
        findings.append(
            HypothesisGuardrailFinding(
                category="Graph inference boundary",
                check_id="missing_graph",
                severity="high",
                artifact_path=str(root),
                message="knowledge_graph.json was not produced.",
            )
        )
        allowed = _empty_allowed_refs()
    else:
        allowed = _allowed_refs(graph, root)
        findings.extend(_graph_boundary_findings(graph, root))

    for artifact in artifacts:
        findings.extend(_text_guardrail_findings(artifact))
        if artifact.json_payload is not None:
            findings.extend(_json_reference_findings(artifact, allowed))
            findings.extend(_json_boundary_findings(artifact, allowed))
        if artifact.is_codex_output:
            findings.extend(_codex_grounding_findings(artifact, allowed))

    report = HypothesisGuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=HYPOTHESIS_GUARDRAIL_CATEGORIES,
        findings=_dedupe_findings(findings),
    )
    _write_hypothesis_guardrail_audit_reports(report)
    return report


def _write_hypothesis_validation_workflow(
    output_dir: Path,
    *,
    fixture: HypothesisValidationFixture,
) -> dict[str, Any]:
    graph = _synthetic_hypothesis_graph()
    write_json_artifact(output_dir / "knowledge_graph.json", graph.model_dump(mode="json"))

    graph_store = KnowledgeGraphStore(output_dir / "graph-store")
    graph_store.save(graph, actor="hypothesis-validation", reason="v1.6_hypothesis_validation")
    hypotheses = generate_hypothesis_candidates(graph_store, mechanism_hypotheses=graph.mechanisms)
    if not hypotheses:
        raise RuntimeError("synthetic hypothesis validation graph produced no hypotheses")

    gaps = analyze_evidence_gaps_for_hypotheses(hypotheses, graph=graph)
    criteria = {
        hypothesis.hypothesis_id: build_falsification_criteria(hypothesis)
        for hypothesis in hypotheses
    }
    questions = {
        hypothesis.hypothesis_id: plan_research_questions(
            hypothesis,
            evidence_gaps=gaps.get(hypothesis.hypothesis_id, []),
            criteria=criteria.get(hypothesis.hypothesis_id, []),
        )
        for hypothesis in hypotheses
    }
    ranked = rank_research_hypotheses(hypotheses, evidence_gaps_by_hypothesis=gaps)
    if fixture == "invented_relation":
        ranked = [
            ranked[0].model_copy(
                update={
                    "supporting_relation_ids": [
                        *ranked[0].supporting_relation_ids,
                        "rel:invented-validation",
                    ]
                }
            ),
            *ranked[1:],
        ]

    store_path = output_dir / "hypotheses.sqlite"
    if store_path.exists():
        store_path.unlink()
    store = HypothesisStore(store_path)
    for hypothesis in ranked:
        store.create_hypothesis(hypothesis)
        for gap in gaps.get(hypothesis.hypothesis_id, []):
            store.add_evidence_gap(gap)
        for criterion in criteria.get(hypothesis.hypothesis_id, []):
            store.add_falsification_criterion(criterion)
        for question in questions.get(hypothesis.hypothesis_id, []):
            store.add_research_question(question)
    review_target = ranked[0]
    HypothesisReviewService(store).record_decision(
        review_target.hypothesis_id,
        reviewer_id="hypothesis-validation-reviewer",
        decision="needs_more_evidence",
        rationale="Synthetic validation review requests more graph-backed context.",
        confidence=0.7,
    )
    reviewed = store.list_hypotheses()
    lifecycle_events = store.list_lifecycle_events()
    generation_run = HypothesisGenerationRun(
        generation_run_id="hypothesis-validation-v1-6",
        project_id="validation-project-v16",
        program_id="validation-program-v16",
        graph_build_id=graph.graph_id,
        input_artifact_ids=["artifact:hypothesis-validation"],
        hypothesis_count=len(reviewed),
        accepted_count=0,
        rejected_count=0,
        completed_at=datetime.now(UTC),
        metadata={"validation": "v1.6", "fixture": fixture},
    )
    store.add_generation_run(generation_run)

    _write_hypothesis_artifacts(
        output_dir,
        hypotheses=reviewed,
        gaps=gaps,
        criteria=criteria,
        questions=questions,
        lifecycle_events=lifecycle_events,
        generation_run=generation_run,
        graph=graph,
        fixture=fixture,
    )
    return {
        "hypotheses": reviewed,
        "gaps": gaps,
        "criteria": criteria,
        "questions": questions,
        "lifecycle_events": lifecycle_events,
    }


def _write_hypothesis_artifacts(
    output_dir: Path,
    *,
    hypotheses: list[ResearchHypothesis],
    gaps: dict[str, list[EvidenceGap]],
    criteria: dict[str, list[FalsificationCriterion]],
    questions: dict[str, list[TestableResearchQuestion]],
    lifecycle_events: list[Any],
    generation_run: HypothesisGenerationRun,
    graph: KnowledgeGraph,
    fixture: HypothesisValidationFixture,
) -> None:
    write_json_artifact(
        output_dir / "hypotheses.json",
        {
            "hypotheses": [hypothesis.model_dump(mode="json") for hypothesis in hypotheses],
            "generation_run": generation_run.model_dump(mode="json"),
            "boundaries": [
                "hypotheses are not evidence",
                "graph inference is not evidence",
                "generated molecules remain computational hypotheses",
            ],
        },
    )
    write_json_artifact(
        output_dir / "ranked_hypotheses.json",
        {"hypotheses": [hypothesis.model_dump(mode="json") for hypothesis in hypotheses]},
    )
    write_json_artifact(
        output_dir / "evidence_gaps.json",
        {
            "evidence_gaps": [
                gap.model_dump(mode="json") for items in gaps.values() for gap in items
            ]
        },
    )
    write_json_artifact(
        output_dir / "falsification_criteria.json",
        {
            "falsification_criteria": [
                criterion.model_dump(mode="json")
                for items in criteria.values()
                for criterion in items
            ]
        },
    )
    write_json_artifact(
        output_dir / "research_questions.json",
        {
            "research_questions": [
                question.model_dump(mode="json")
                for items in questions.values()
                for question in items
            ]
        },
    )
    write_json_artifact(
        output_dir / "hypothesis_lifecycle.json",
        {"events": [event.model_dump(mode="json") for event in lifecycle_events]},
    )
    write_json_artifact(
        output_dir / "codex_hypothesis_explanation.json",
        _safe_codex_explanation_payload(hypotheses[0], graph),
    )
    report = render_hypothesis_report_markdown(
        hypotheses,
        evidence_gaps_by_hypothesis=gaps,
        criteria_by_hypothesis=criteria,
        questions_by_hypothesis=questions,
        lifecycle_events=lifecycle_events,
    )
    (output_dir / "hypothesis_report.md").write_text(report, encoding="utf-8")
    if fixture == "protocol_text":
        write_markdown_artifact(
            output_dir / "hypothesis_validation_note.md",
            "Hypothesis Validation Note",
            ["Use a step-by-step lab protocol with reagent concentration and incubation time."],
        )
    if fixture == "generated_activity_claim":
        write_markdown_artifact(
            output_dir / "hypothesis_validation_note.md",
            "Hypothesis Validation Note",
            ["The generated molecule is active and is safe for follow-up."],
        )


def _safe_codex_explanation_payload(
    hypothesis: ResearchHypothesis,
    graph: KnowledgeGraph,
) -> dict[str, Any]:
    entity_ids = [
        *hypothesis.disease_entity_ids,
        *hypothesis.target_entity_ids,
        *hypothesis.molecule_entity_ids,
        *hypothesis.generated_molecule_entity_ids,
    ]
    return {
        "assistant_output": True,
        "hypothesis_id": hypothesis.hypothesis_id,
        "entity_ids": sorted(set(entity_ids)),
        "relation_ids": [
            *hypothesis.supporting_relation_ids,
            *hypothesis.contradicting_relation_ids,
        ],
        "provenance_ids": [provenance.provenance_id for provenance in graph.provenance[:2]],
        "artifact_ids": ["artifact:hypothesis-validation", "knowledge_graph.json"],
        "summary": (
            "Codex explanation is wording-only and cites only deterministic graph-backed "
            "hypothesis references. It is not evidence and does not approve the hypothesis."
        ),
    }


def _synthetic_hypothesis_graph() -> KnowledgeGraph:
    old = datetime.now(UTC) - timedelta(days=45)
    now = datetime.now(UTC)
    entities = [
        _entity("disease:validation", "disease", "Synthetic validation disease"),
        _entity("target:VAL1", "target", "VAL1"),
        _entity("mechanism:val1-pathway", "mechanism", "VAL1 pathway modulation"),
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity("molecule:untested", "molecule", "Untested candidate"),
        _entity(
            "generated_molecule:analog",
            "generated_molecule",
            "Generated analog",
            metadata={"readiness_score": 0.9, "design_score": 0.88},
        ),
        _entity("model_prediction:seed-positive", "model_prediction", "Seed model prediction"),
        _entity("assay_result:seed-negative", "assay_result", "Seed negative result"),
        _entity("assay_result:cross-a-positive", "assay_result", "Cross-program A result"),
        _entity("assay_result:cross-b-positive", "assay_result", "Cross-program B result"),
        _entity("review_decision:seed-old", "review_decision", "Old seed review decision"),
        _entity("scaffold:core-a", "scaffold", "Core A"),
        _entity("molecule:risk-a", "molecule", "Risk candidate A"),
        _entity("molecule:risk-b", "molecule", "Risk candidate B"),
        _entity("developability_alert:liability", "developability_alert", "Repeated liability"),
    ]
    relations = [
        _relation(
            "rel:disease-target",
            "disease:validation",
            "associated_with",
            "target:VAL1",
            "evidence_backed",
            confidence=0.91,
            direction="supportive",
            source_record_ids=["record:disease-target"],
        ),
        _relation(
            "rel:target-mechanism",
            "target:VAL1",
            "has_mechanism",
            "mechanism:val1-pathway",
            "literature",
            confidence=0.82,
            direction="supportive",
            source_record_ids=["record:mechanism"],
        ),
        _relation(
            "rel:untested-target",
            "molecule:untested",
            "targets",
            "target:VAL1",
            "literature",
            confidence=0.84,
            direction="supportive",
            source_record_ids=["record:untested-target"],
        ),
        _relation(
            "rel:generated-lineage",
            "generated_molecule:analog",
            "generated_from",
            "molecule:seed",
            "generated_lineage",
            confidence=0.9,
            direction="supportive",
            metadata={"readiness_score": 0.9, "design_score": 0.88},
        ),
        _relation(
            "rel:seed-model-positive",
            "molecule:seed",
            "predicted_by_model",
            "model_prediction:seed-positive",
            "model_prediction",
            confidence=0.88,
            direction="supportive",
            metadata={
                "target_entity_id": "target:VAL1",
                "model_prediction_id": "model_prediction:seed-positive",
            },
        ),
        _relation(
            "rel:seed-assay-negative",
            "molecule:seed",
            "produced_result",
            "assay_result:seed-negative",
            "experimental",
            confidence=0.9,
            direction="contradictory",
            metadata={
                "target_entity_id": "target:VAL1",
                "outcome_label": "negative",
                "qc_status": "passed",
                "program_id": "validation-program-v16",
            },
            created_at=now,
        ),
        _relation(
            "rel:seed-old-review",
            "molecule:seed",
            "reviewed_as",
            "review_decision:seed-old",
            "review",
            confidence=0.72,
            direction="supportive",
            metadata={"review_decision_id": "review_decision:seed-old"},
            created_at=old,
            updated_at=old,
        ),
        _relation(
            "rel:cross-a-positive",
            "molecule:risk-a",
            "produced_result",
            "assay_result:cross-a-positive",
            "experimental",
            confidence=0.86,
            direction="supportive",
            metadata={
                "target_entity_id": "target:VAL1",
                "outcome_label": "positive",
                "qc_status": "passed",
                "program_id": "program-a",
            },
        ),
        _relation(
            "rel:cross-b-positive",
            "molecule:risk-b",
            "produced_result",
            "assay_result:cross-b-positive",
            "experimental",
            confidence=0.87,
            direction="supportive",
            metadata={
                "target_entity_id": "target:VAL1",
                "outcome_label": "positive",
                "qc_status": "passed",
                "program_id": "program-b",
            },
        ),
        _relation(
            "rel:risk-a-target",
            "molecule:risk-a",
            "targets",
            "target:VAL1",
            "literature",
            confidence=0.77,
            direction="supportive",
            source_record_ids=["record:risk-a-target"],
        ),
        _relation(
            "rel:risk-b-target",
            "molecule:risk-b",
            "targets",
            "target:VAL1",
            "literature",
            confidence=0.78,
            direction="supportive",
            source_record_ids=["record:risk-b-target"],
        ),
        _relation(
            "rel:risk-a-scaffold",
            "molecule:risk-a",
            "has_scaffold",
            "scaffold:core-a",
            "computational",
            confidence=0.86,
            direction="supportive",
        ),
        _relation(
            "rel:risk-b-scaffold",
            "molecule:risk-b",
            "has_scaffold",
            "scaffold:core-a",
            "computational",
            confidence=0.87,
            direction="supportive",
        ),
        _relation(
            "rel:risk-a-developability",
            "molecule:risk-a",
            "has_developability_risk",
            "developability_alert:liability",
            "computational",
            confidence=0.82,
            direction="risk",
            metadata={"risk_type": "developability"},
        ),
        _relation(
            "rel:risk-b-developability",
            "molecule:risk-b",
            "has_developability_risk",
            "developability_alert:liability",
            "computational",
            confidence=0.83,
            direction="risk",
            metadata={"risk_type": "developability"},
        ),
    ]
    provenance = [
        GraphProvenance(
            provenance_id="prov:hypothesis-validation-graph",
            source_type="generated_artifact",
            source_artifact_id="artifact:hypothesis-validation",
            source_record_id="record:hypothesis-validation",
            transformation="Synthetic deterministic V1.6 hypothesis validation fixture.",
            confidence=1.0,
        ),
        GraphProvenance(
            provenance_id="prov:hypothesis-validation-assay",
            source_type="imported_assay_result",
            source_artifact_id="artifact:hypothesis-validation",
            source_record_id="record:assay-validation",
            transformation="Synthetic assay result context for guardrail validation.",
            confidence=1.0,
        ),
    ]
    return KnowledgeGraph(
        graph_id="kg-hypothesis-validation-v1-6",
        entities=entities,
        relations=relations,
        provenance=provenance,
        metadata={"validation": "v1.6", "synthetic": True},
    )


def _entity(
    entity_id: str,
    entity_type: str,
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> GraphEntity:
    return GraphEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        source_artifact_ids=["artifact:hypothesis-validation"],
        provenance_refs=["prov:hypothesis-validation-graph"],
        metadata=metadata or {},
    )


def _relation(
    relation_id: str,
    subject: str,
    predicate: str,
    object_: str,
    relation_type: str,
    *,
    confidence: float,
    direction: str,
    source_record_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> GraphRelation:
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_,
        relation_type=relation_type,
        confidence=confidence,
        direction=direction,
        source_artifact_ids=["artifact:hypothesis-validation"],
        source_record_ids=source_record_ids or [f"record:{relation_id.removeprefix('rel:')}"],
        metadata=metadata or {},
        created_at=created_at or datetime.now(UTC),
        updated_at=updated_at or created_at or datetime.now(UTC),
    )


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
        if "graph-store" in path.parts:
            continue
        if path.suffix.lower() not in {".json", ".md", ".txt"}:
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


def _empty_allowed_refs() -> dict[str, set[str]]:
    return {
        "hypothesis_ids": set(),
        "entity_ids": set(),
        "relation_ids": set(),
        "provenance_ids": set(),
        "artifact_ids": set(),
        "assay_result_ids": set(),
        "model_prediction_ids": set(),
        "review_decision_ids": set(),
    }


def _allowed_refs(graph: KnowledgeGraph, root: Path) -> dict[str, set[str]]:
    artifact_ids = {
        "artifact:hypothesis-validation",
        "knowledge_graph.json",
        "hypotheses.json",
        "research_questions.json",
        "evidence_gaps.json",
        "falsification_criteria.json",
        "hypothesis_report.md",
        graph.graph_id,
        f"graph:{graph.graph_id}",
    }
    for entity in graph.entities:
        artifact_ids.update(entity.source_artifact_ids)
    for relation in graph.relations:
        artifact_ids.update(relation.source_artifact_ids)
    for provenance in graph.provenance:
        if provenance.source_artifact_id:
            artifact_ids.add(provenance.source_artifact_id)
    hypothesis_ids = _hypothesis_ids_from_root(root)
    return {
        "hypothesis_ids": hypothesis_ids,
        "entity_ids": {entity.entity_id for entity in graph.entities},
        "relation_ids": {relation.relation_id for relation in graph.relations},
        "provenance_ids": {provenance.provenance_id for provenance in graph.provenance},
        "artifact_ids": artifact_ids,
        "assay_result_ids": {
            entity.entity_id for entity in graph.entities if entity.entity_type == "assay_result"
        },
        "model_prediction_ids": {
            entity.entity_id
            for entity in graph.entities
            if entity.entity_type == "model_prediction"
        },
        "review_decision_ids": {
            entity.entity_id
            for entity in graph.entities
            if entity.entity_type == "review_decision"
        },
    }


def _hypothesis_ids_from_root(root: Path) -> set[str]:
    path = root / "hypotheses.json"
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["hypothesis_id"])
        for item in payload.get("hypotheses", [])
        if isinstance(item, dict) and item.get("hypothesis_id")
    }


def _graph_boundary_findings(
    graph: KnowledgeGraph,
    root: Path,
) -> list[HypothesisGuardrailFinding]:
    findings: list[HypothesisGuardrailFinding] = []
    for relation in graph.relations:
        if relation.relation_type == "inferred":
            if not relation.metadata.get("not_evidence"):
                findings.append(
                    HypothesisGuardrailFinding(
                        category="Graph inference boundary",
                        check_id="graph_inference_not_marked_not_evidence",
                        severity="high",
                        artifact_path=str(root / "knowledge_graph.json"),
                        message=(
                            f"Inferred relation {relation.relation_id} must be marked "
                            "not_evidence."
                        ),
                    )
                )
            if relation.evidence_item_ids:
                findings.append(
                    HypothesisGuardrailFinding(
                        category="Hypothesis evidence boundary",
                        check_id="graph_inference_created_evidence",
                        severity="critical",
                        artifact_path=str(root / "knowledge_graph.json"),
                        message="Graph inference must not create EvidenceItem records.",
                    )
                )
    return findings


def _text_guardrail_findings(
    artifact: _ArtifactSnapshot,
) -> list[HypothesisGuardrailFinding]:
    findings = []
    text = _strip_boundary_disclaimers(artifact.text)
    for message in detect_hypothesis_guardrail_violations(text):
        findings.append(
            HypothesisGuardrailFinding(
                category=_category_for_message(message),
                check_id="forbidden_hypothesis_output",
                severity="critical",
                artifact_path=str(artifact.path),
                message=message,
                excerpt=_excerpt(text),
            )
        )
    return findings


def _json_reference_findings(
    artifact: _ArtifactSnapshot,
    allowed: dict[str, set[str]],
) -> list[HypothesisGuardrailFinding]:
    if not artifact.is_codex_output:
        return []
    observed = observed_hypothesis_references(artifact.text, artifact.json_payload)
    findings: list[HypothesisGuardrailFinding] = []
    for bucket in ["entity_ids", "relation_ids", "provenance_ids", "artifact_ids"]:
        for value in sorted(observed[bucket] - allowed[bucket]):
            findings.append(
                HypothesisGuardrailFinding(
                    category="Codex grounding",
                    check_id=f"unknown_{bucket[:-1]}",
                    severity="critical",
                    artifact_path=str(artifact.path),
                    message=f"Unknown {bucket[:-1].replace('_', ' ')} referenced: {value}",
                )
            )
    return findings


def _json_boundary_findings(
    artifact: _ArtifactSnapshot,
    allowed: dict[str, set[str]],
) -> list[HypothesisGuardrailFinding]:
    payload = artifact.json_payload
    if not isinstance(payload, dict):
        return []
    findings: list[HypothesisGuardrailFinding] = []
    for hypothesis in payload.get("hypotheses", []):
        if isinstance(hypothesis, dict):
            findings.extend(_hypothesis_payload_findings(hypothesis, artifact, allowed))
    for question in payload.get("research_questions", []):
        if not isinstance(question, dict):
            continue
        text = " ".join(str(question.get(key, "")) for key in _QUESTION_TEXT_KEYS)
        for message in detect_hypothesis_guardrail_violations(text):
            findings.append(
                HypothesisGuardrailFinding(
                    category="Research question boundary",
                    check_id="research_question_protocol_detail",
                    severity="critical",
                    artifact_path=str(artifact.path),
                    message=message,
                    excerpt=_excerpt(text),
                )
            )
        if question.get("forbidden_detail_check") is not True:
            findings.append(
                HypothesisGuardrailFinding(
                    category="Research question boundary",
                    check_id="research_question_forbidden_detail_check_failed",
                    severity="critical",
                    artifact_path=str(artifact.path),
                    message="Research questions must pass forbidden-detail validation.",
                )
            )
    return findings


_QUESTION_TEXT_KEYS = (
    "question_text",
    "high_level_validation_category",
    "expected_observation_if_supported",
    "expected_observation_if_not_supported",
)


def _hypothesis_payload_findings(
    hypothesis: dict[str, Any],
    artifact: _ArtifactSnapshot,
    allowed: dict[str, set[str]],
) -> list[HypothesisGuardrailFinding]:
    findings: list[HypothesisGuardrailFinding] = []
    text = " ".join(str(hypothesis.get(key, "")) for key in ["title", "statement"])
    for message in detect_hypothesis_guardrail_violations(text):
        findings.append(
            HypothesisGuardrailFinding(
                category="Unsupported claim boundary",
                check_id="hypothesis_forbidden_claim",
                severity="critical",
                artifact_path=str(artifact.path),
                message=message,
                excerpt=_excerpt(text),
            )
        )
    for key in _ENTITY_REF_KEYS:
        for value in _as_list(hypothesis.get(key)):
            if value not in allowed["entity_ids"]:
                findings.append(
                    HypothesisGuardrailFinding(
                        category="Codex grounding",
                        check_id="unknown_hypothesis_entity",
                        severity="critical",
                        artifact_path=str(artifact.path),
                        message=f"Unknown entity referenced by hypothesis: {value}",
                    )
                )
    for key in ["supporting_relation_ids", "contradicting_relation_ids"]:
        for value in _as_list(hypothesis.get(key)):
            if value not in allowed["relation_ids"]:
                findings.append(
                    HypothesisGuardrailFinding(
                        category="Codex grounding",
                        check_id="unknown_hypothesis_relation",
                        severity="critical",
                        artifact_path=str(artifact.path),
                        message=f"Unknown relation referenced by hypothesis: {value}",
                    )
                )
    for value in _as_list(hypothesis.get("assay_result_ids")):
        if value not in allowed["assay_result_ids"]:
            findings.append(
                HypothesisGuardrailFinding(
                    category="Codex grounding",
                    check_id="unknown_assay_result",
                    severity="critical",
                    artifact_path=str(artifact.path),
                    message=f"Unknown assay result referenced by hypothesis: {value}",
                )
            )
    metadata = hypothesis.get("metadata")
    if isinstance(metadata, dict) and metadata.get("creates_evidence") is True:
        findings.append(
            HypothesisGuardrailFinding(
                category="Hypothesis evidence boundary",
                check_id="hypothesis_creates_evidence",
                severity="critical",
                artifact_path=str(artifact.path),
                message="Hypotheses must not create EvidenceItem records.",
            )
        )
    if hypothesis.get("hypothesis_type") == "generated_molecule":
        warnings = " ".join(_as_list(hypothesis.get("warnings"))).lower()
        if "hypothesis" not in warnings or "evidence" not in warnings:
            findings.append(
                HypothesisGuardrailFinding(
                    category="Generated molecule boundary",
                    check_id="generated_hypothesis_missing_warning",
                    severity="high",
                    artifact_path=str(artifact.path),
                    message=(
                        "Generated-molecule hypotheses must remain computational "
                        "hypotheses with no-direct-evidence warnings."
                    ),
                )
            )
    return findings


_ENTITY_REF_KEYS = (
    "disease_entity_ids",
    "target_entity_ids",
    "molecule_entity_ids",
    "generated_molecule_entity_ids",
    "scaffold_entity_ids",
    "mechanism_entity_ids",
)


def _codex_grounding_findings(
    artifact: _ArtifactSnapshot,
    allowed: dict[str, set[str]],
) -> list[HypothesisGuardrailFinding]:
    if not isinstance(artifact.json_payload, dict):
        return [
            HypothesisGuardrailFinding(
                category="Codex grounding",
                check_id="codex_output_not_json",
                severity="critical",
                artifact_path=str(artifact.path),
                message="Codex hypothesis output must be JSON.",
            )
        ]
    payload = artifact.json_payload
    findings: list[HypothesisGuardrailFinding] = []
    if payload.get("hypothesis_id") not in allowed["hypothesis_ids"]:
        findings.append(
            HypothesisGuardrailFinding(
                category="Codex grounding",
                check_id="codex_unknown_hypothesis",
                severity="critical",
                artifact_path=str(artifact.path),
                message="Codex output must cite a known hypothesis_id.",
            )
        )
    required = ["entity_ids", "relation_ids", "provenance_ids", "artifact_ids"]
    for key in required:
        if not payload.get(key):
            findings.append(
                HypothesisGuardrailFinding(
                    category="Codex grounding",
                    check_id=f"codex_missing_{key}",
                    severity="critical",
                    artifact_path=str(artifact.path),
                    message=f"Codex output must cite {key}.",
                )
            )
    return findings


def _write_hypothesis_guardrail_audit_reports(
    report: HypothesisGuardrailAuditReport,
) -> None:
    write_json_artifact(report.root_dir / "hypothesis_guardrail_audit.json", report.as_dict())
    lines = [
        f"- Status: `{report.status}`",
        f"- Artifacts audited: {report.artifact_count}",
        f"- Findings: {len(report.findings)}",
        "",
        "## Findings",
    ]
    if report.findings:
        lines.extend(
            f"- `{finding.severity}` `{finding.check_id}` in `{finding.artifact_path}`: "
            f"{finding.message}"
            for finding in report.findings
        )
    else:
        lines.append("- none")
    write_markdown_artifact(
        report.root_dir / "hypothesis_guardrail_audit.md",
        "V1.6 Hypothesis Guardrail Audit",
        lines,
    )


def _dedupe_findings(
    findings: list[HypothesisGuardrailFinding],
) -> list[HypothesisGuardrailFinding]:
    deduped: dict[tuple[str, str, str, str], HypothesisGuardrailFinding] = {}
    for finding in findings:
        key = (finding.check_id, finding.artifact_path, finding.message, finding.excerpt)
        deduped[key] = finding
    return sorted(
        deduped.values(),
        key=lambda item: (item.severity, item.category, item.check_id, item.artifact_path),
    )


def _category_for_message(message: str) -> str:
    lowered = message.lower()
    if "protocol" in lowered or "temperature" in lowered or "concentration" in lowered:
        return "Protocol boundary"
    if "synthesis" in lowered or "reagent" in lowered:
        return "Synthesis boundary"
    if "dosing" in lowered or "dose" in lowered:
        return "Dosing boundary"
    if "medical advice" in lowered or "patient treatment" in lowered:
        return "Medical advice boundary"
    if "claim" in lowered or "activity" in lowered or "safety" in lowered:
        return "Unsupported claim boundary"
    return "Hypothesis evidence boundary"


def _excerpt(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    return compact[:limit]


def _strip_boundary_disclaimers(text: str) -> str:
    safe_fragments = (
        "no ",
        "not ",
        "does not ",
        "do not ",
        "must not ",
        "should not ",
        "cannot ",
        "without ",
        "remain computational hypotheses",
    )
    guarded_terms = (
        "evidence",
        "protocol",
        "synthesis",
        "reagent",
        "concentration",
        "temperature",
        "incubation",
        "dosing",
        "dose",
        "medical advice",
        "patient",
        "clinical",
        "causality",
        "efficacy",
        "safety",
        "activity",
        "active",
        "binding",
        "binds",
        "inhibits",
        "activates",
    )
    retained = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(fragment in lowered for fragment in safe_fragments) and any(
            term in lowered for term in guarded_terms
        ):
            continue
        retained.append(line)
    return "\n".join(retained)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


__all__ = [
    "HYPOTHESIS_GUARDRAIL_CATEGORIES",
    "HYPOTHESIS_VALIDATION_STEPS",
    "HypothesisGuardrailAuditReport",
    "HypothesisGuardrailFinding",
    "HypothesisValidationReport",
    "run_hypothesis_guardrail_audit",
    "run_hypothesis_validation",
]
