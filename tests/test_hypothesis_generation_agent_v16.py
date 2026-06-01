from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.agents import HypothesisGenerationAgent
from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.config import RankerConfig
from molecule_ranker.hypotheses.store import HypothesisStore
from molecule_ranker.knowledge_graph.schemas import (
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    KnowledgeGraph,
)
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator


def test_hypothesis_generation_disabled_noop(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Parkinson disease",
        output_dir=tmp_path,
        config={"enable_hypothesis_generation": False, "results_dir": str(tmp_path)},
    )

    result = HypothesisGenerationAgent().run(context)

    assert "hypothesis_generation" not in result.config
    assert not (tmp_path / "hypotheses").exists()
    assert result.traces[-1].agent_name == "HypothesisGenerationAgent"
    assert result.traces[-1].metadata["enabled"] is False


def test_hypothesis_generation_enabled_writes_artifacts(tmp_path: Path) -> None:
    context = _context(tmp_path, _graph())

    result = HypothesisGenerationAgent().run(context)
    metadata = result.config["hypothesis_generation"]
    hypotheses_path = Path(metadata["artifact_paths"]["hypotheses"])
    questions_path = Path(metadata["artifact_paths"]["research_questions"])
    criteria_path = Path(metadata["artifact_paths"]["falsification_criteria"])
    gaps_path = Path(metadata["artifact_paths"]["evidence_gaps"])
    lifecycle_path = Path(metadata["artifact_paths"]["hypothesis_lifecycle"])
    report_path = Path(metadata["artifact_paths"]["hypothesis_report"])

    assert hypotheses_path.exists()
    assert questions_path.exists()
    assert criteria_path.exists()
    assert gaps_path.exists()
    assert lifecycle_path.exists()
    assert report_path.exists()
    payload = json.loads(hypotheses_path.read_text())
    question_payload = json.loads(questions_path.read_text())
    criteria_payload = json.loads(criteria_path.read_text())
    gaps_payload = json.loads(gaps_path.read_text())
    lifecycle_payload = json.loads(lifecycle_path.read_text())
    report = report_path.read_text()
    assert payload["hypotheses"]
    assert question_payload["questions"]
    assert criteria_payload["falsification_criteria"]
    assert gaps_payload["evidence_gaps"]
    assert lifecycle_payload["lifecycle_events"]
    assert "## Hypothesis Summary" in report
    assert HypothesisStore(metadata["store_path"]).list_hypotheses()


def test_hypothesis_report_sections_and_safety_text(tmp_path: Path) -> None:
    result = HypothesisGenerationAgent().run(_context(tmp_path, _graph()))
    report = Path(
        result.config["hypothesis_generation"]["artifact_paths"]["hypothesis_report"]
    ).read_text()

    for section in [
        "Hypothesis Summary",
        "Top Hypotheses",
        "Mechanistic Hypotheses",
        "Generated-Molecule Hypotheses",
        "Contradiction-Resolution Hypotheses",
        "Evidence Gaps",
        "Falsification Criteria",
        "Testable Research Questions",
        "Review Status",
        "Limitations",
    ]:
        assert f"## {section}" in report
    for disclaimer in [
        "Hypotheses are not evidence.",
        "Questions are not protocols.",
        "No synthesis instructions are provided.",
        "No lab protocols are provided.",
        "No dosing guidance is provided.",
        "No clinical claims are made.",
        "Generated molecules remain computational hypotheses.",
    ]:
        assert disclaimer in report
    forbidden_procedural_patterns = ["step 1", "10 nM", "30 minutes", "mg/kg"]
    assert not any(pattern in report for pattern in forbidden_procedural_patterns)
    assert "Generated no-direct-evidence warning" in report


def test_hypothesis_generation_codex_drafting_optional_accepts_safe_wording(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        _graph(),
        use_codex=True,
        provider=_FakeCodexProvider(
            {
                "statement": (
                    "Hypothesis for review: source-backed context leaves a "
                    "testable evidence gap."
                )
            }
        ),
    )

    result = HypothesisGenerationAgent().run(context)
    payload = json.loads(
        Path(result.config["hypothesis_generation"]["artifact_paths"]["hypotheses"]).read_text()
    )

    assert result.config["hypothesis_generation"]["codex_drafting"]["accepted_count"] >= 1
    assert payload["hypotheses"][0]["statement"].startswith("Hypothesis for review")
    assert payload["hypotheses"][0]["metadata"]["codex_draft"]["status"] == "accepted"


def test_hypothesis_generation_unsafe_codex_output_rejected(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        _graph(),
        use_codex=True,
        provider=_FakeCodexProvider(
            {
                "statement": (
                    "Use a protocol with reagent concentration 10 nM for 30 minutes."
                )
            }
        ),
    )

    result = HypothesisGenerationAgent().run(context)
    payload = json.loads(
        Path(result.config["hypothesis_generation"]["artifact_paths"]["hypotheses"]).read_text()
    )
    statement = payload["hypotheses"][0]["statement"].lower()

    assert result.config["hypothesis_generation"]["codex_drafting"]["fallback_count"] >= 1
    assert "reagent concentration" not in statement
    assert payload["hypotheses"][0]["metadata"]["codex_draft"]["status"] == "fallback"


def test_generated_hypothesis_requires_review(tmp_path: Path) -> None:
    context = _context(tmp_path, _graph())

    result = HypothesisGenerationAgent().run(context)
    payload = json.loads(
        Path(result.config["hypothesis_generation"]["artifact_paths"]["hypotheses"]).read_text()
    )
    generated = next(
        hypothesis
        for hypothesis in payload["hypotheses"]
        if hypothesis["hypothesis_type"] == "generated_molecule"
    )

    assert generated["status"] == "under_review"
    assert generated["metadata"]["ranking"]["requires_review_before_follow_up"] is True
    assert generated["hypothesis_id"] in result.config["hypothesis_generation"][
        "generated_hypothesis_ids_requiring_review"
    ]


def test_hypothesis_generation_agent_pipeline_placement() -> None:
    config = RankerConfig(enable_hypothesis_generation=True)
    orchestrator = MoleculeRankerOrchestrator(config=config)
    names = [agent.name for agent in orchestrator.agents]

    assert names.index("EvidenceScoringAgent") < names.index("HypothesisGenerationAgent")
    assert names.index("HypothesisGenerationAgent") < names.index("PortfolioOptimizationAgent")
    assert names.index("HypothesisGenerationAgent") < names.index("ReviewWorkspaceAgent")
    assert names.index("HypothesisGenerationAgent") < names.index("ReportWriterAgent")


def _context(
    tmp_path: Path,
    graph: KnowledgeGraph,
    *,
    use_codex: bool = False,
    provider: Any | None = None,
) -> PipelineContext:
    config: dict[str, Any] = {
        "enable_hypothesis_generation": True,
        "use_codex_hypothesis_drafting": use_codex,
        "max_hypotheses": 10,
        "max_questions_per_hypothesis": 2,
        "strict_hypothesis_guardrails": True,
        "require_human_review_for_generated_hypotheses": True,
        "knowledge_graph": graph.model_dump(mode="json"),
        "results_dir": str(tmp_path),
    }
    if provider is not None:
        config["codex_hypothesis_drafting_provider"] = provider
    return PipelineContext(
        disease_input="Parkinson disease",
        output_dir=tmp_path,
        config=config,
    )


def _graph() -> KnowledgeGraph:
    entities = [
        _entity("molecule:seed", "molecule", "Seed molecule"),
        _entity(
            "generated_molecule:analog",
            "generated_molecule",
            "Generated analog",
            metadata={"design_score": 0.91, "readiness_score": 0.88},
        ),
    ]
    relations = [
        GraphRelation(
            relation_id="rel:generated-lineage",
            subject_entity_id="generated_molecule:analog",
            predicate="generated_from",
            object_entity_id="molecule:seed",
            relation_type="generated_lineage",
            confidence=0.9,
            source_artifact_ids=["artifact:kg"],
        )
    ]
    return KnowledgeGraph(
        graph_id="graph-hypothesis-agent",
        entities=entities,
        relations=relations,
        provenance=[
            GraphProvenance(
                provenance_id="prov:kg",
                source_type="artifact",
                source_artifact_id="artifact:kg",
                transformation="Synthetic graph fixture provenance.",
                confidence=1.0,
            )
        ],
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
        source_artifact_ids=["artifact:kg"],
        metadata=metadata or {},
    )


class _FakeCodexProvider:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_json={
                **self.payload,
                "hypothesis_id": task.metadata["hypothesis_id"],
                "entity_ids": ["generated_molecule:analog", "molecule:seed"],
                "relation_ids": ["rel:generated-lineage"],
                "provenance_ids": ["prov:kg"],
                "artifact_ids": ["artifact:kg"],
            },
        )
