from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    EvidenceItem,
    MoleculeCandidate,
    ScoreBreakdown,
    Target,
)

RETRIEVED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _evidence(source: str, record_id: str, evidence_type: str) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        source_record_id=record_id,
        title=f"{source} record",
        url=f"https://example.org/{record_id}",
        evidence_type=evidence_type,
        summary=f"Retrieved {source} evidence.",
        confidence=0.8,
        retrieval_timestamp=RETRIEVED_AT,
        metadata={"query": "Parkinson disease"},
    )


def _scored_context(tmp_path) -> PipelineContext:
    disease = Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        synonyms=[],
        identifiers={"open_targets": "MONDO_0005180"},
        description=None,
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[_evidence("Open Targets", "MONDO_0005180:ENSG1", "target_disease")],
        mechanism="Retrieved target mechanism.",
    )
    breakdown = ScoreBreakdown(
        disease_target_relevance=0.8,
        molecule_target_evidence=0.9,
        mechanism_plausibility=0.8,
        clinical_precedence=1.0,
        safety_prior=0.8,
        data_quality=0.8,
        novelty_or_repurposing_value=0.7,
        final_score=0.835,
        confidence=0.7,
        explanation="Retrieved evidence links the candidate to MAOB.",
    )
    candidate = MoleculeCandidate(
        name="Evidence-backed candidate",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL_TEST"},
        known_targets=["MAOB"],
        development_status="approved",
        mechanism_of_action="MAOB inhibitor",
        evidence=[_evidence("ChEMBL", "mec-1", "mechanism")],
        score=0.835,
        score_breakdown=breakdown,
        warnings=["Scores are heuristic and require experimental validation."],
    )
    return PipelineContext(
        disease_input="Parkinson disease",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        traces=[
            AgentTrace(
                agent_name="EvidenceScoringAgent",
                input_summary="Scoring input.",
                output_summary="Scored one candidate.",
                warnings=[],
                metadata={},
            )
        ],
        config={"results_dir": str(tmp_path)},
    )


def test_report_writer_creates_success_artifacts(tmp_path):
    context = _scored_context(tmp_path)

    updated = ReportWriterAgent().run(context)

    output_dir = tmp_path / "parkinson-disease"
    assert updated.output_dir == output_dir
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()

    candidates_payload = json.loads((output_dir / "candidates.json").read_text())
    assert candidates_payload["success"] is True
    assert (
        candidates_payload["candidates"][0]["score_breakdown"]["final_score"]
        == pytest.approx(0.835)
    )

    trace_payload = json.loads((output_dir / "trace.json").read_text())
    assert trace_payload["traces"][-1]["agent_name"] == "ReportWriterAgent"
    assert trace_payload["artifacts"]["report_md"].endswith("report.md")

    report = (output_dir / "report.md").read_text()
    assert "# Molecule Ranking Report: Parkinson disease" in report
    assert "## Research-use disclaimer" in report
    assert "## Data provenance" in report
    assert "MONDO_0005180" in report
    assert "Open Targets" in report
    assert "ChEMBL" in report
    assert "2026-01-02T03:04:05+00:00" in report
    assert "## Ranked Candidates" in report
    assert "| Disease-target relevance | 0.800 |" in report
    assert "## Targets Considered" in report
    assert "## Pipeline Trace" in report
    assert "ReportWriterAgent" in report
    assert "Novel molecule generation is not implemented in V0.0." in report
    assert "fixture" not in report.lower()


def test_report_writer_failed_run_does_not_create_success_report(tmp_path):
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=Disease(
            input_name="Parkinson disease",
            canonical_name="Parkinson disease",
            synonyms=[],
            identifiers={"open_targets": "MONDO_0005180"},
            description=None,
        ),
        targets=[],
        candidates=[],
        config={"results_dir": str(tmp_path)},
        output_dir=tmp_path / "parkinson-disease",
    )

    with pytest.raises(NoCandidatesFoundError):
        ReportWriterAgent().run(context)

    assert not (tmp_path / "parkinson-disease" / "report.md").exists()
    assert not (tmp_path / "parkinson-disease" / "candidates.json").exists()
    assert not (tmp_path / "parkinson-disease" / "trace.json").exists()
