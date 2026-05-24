from __future__ import annotations

import json
from typing import Any

import pytest

from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.orchestrator import MoleculeRankerOrchestrator
from molecule_ranker.schemas import Disease, EvidenceItem, Target


class FakeDiseaseSource:
    def resolve_disease(self, disease_name: str) -> Disease:
        return Disease(
            input_name=disease_name,
            canonical_name="Parkinson disease",
            synonyms=[],
            identifiers={"open_targets": "MONDO_0005180"},
            description="Mocked disease record.",
        )


class FailingDiseaseSource:
    def resolve_disease(self, disease_name: str) -> Disease:
        raise DiseaseResolutionError("Disease could not be resolved.")


class FakeTargetSource:
    def __init__(self) -> None:
        self.calls = 0

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        self.calls += 1
        return [
            Target(
                symbol="MAOB",
                name="Monoamine oxidase B",
                disease_relevance_score=0.8,
                evidence=[
                    EvidenceItem(
                        source="Open Targets",
                        source_record_id="MONDO_0005180:ENSG1",
                        title="Target association",
                        evidence_type="target_disease_association",
                        summary="Mocked Open Targets association.",
                        confidence=0.8,
                        metadata={"query": "test"},
                    )
                ],
                mechanism=None,
            )
        ]


class EmptyTargetSource:
    def __init__(self) -> None:
        self.calls = 0

    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        self.calls += 1
        return []


class FailingTargetSource:
    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
        raise TargetDiscoveryError("Target discovery failed.")


class FakeMoleculeSource:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        self.calls += 1
        return [
            {
                "name": "Levodopa",
                "molecule_type": "small_molecule",
                "identifiers": {"chembl": "CHEMBL1009"},
                "known_targets": ["MAOB"],
                "development_status": "max_phase_4",
                "mechanism_of_action": "Dopaminergic mechanism.",
                "target_fit": 0.9,
                "clinical_precedence": 1.0,
                "safety_prior": 0.6,
                "repurposing_value": 0.4,
                "evidence": [
                    EvidenceItem(
                        source="ChEMBL",
                        source_record_id="mec-1",
                        title="Mechanism record",
                        evidence_type="mechanism",
                        summary="Mocked ChEMBL mechanism.",
                        confidence=0.9,
                        metadata={"query": "test"},
                    ).model_dump(mode="json")
                ],
            },
            {
                "name": "Rasagiline",
                "molecule_type": "small_molecule",
                "identifiers": {"chembl": "CHEMBL887"},
                "known_targets": ["MAOB"],
                "development_status": "max_phase_4",
                "mechanism_of_action": "MAO-B inhibition.",
                "target_fit": 0.7,
                "clinical_precedence": 0.8,
                "safety_prior": 0.6,
                "repurposing_value": 0.4,
                "evidence": [
                    EvidenceItem(
                        source="ChEMBL",
                        source_record_id="mec-2",
                        title="Mechanism record",
                        evidence_type="mechanism",
                        summary="Mocked ChEMBL mechanism.",
                        confidence=0.7,
                        metadata={"query": "test"},
                    ).model_dump(mode="json")
                ],
            },
        ]


class FailingMoleculeSource:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        self.calls += 1
        raise MoleculeRetrievalError("Molecule retrieval failed.")


class EmptyMoleculeSource:
    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        return []


class NoOpAnnotationSource:
    source_name = "Test annotation source"

    def annotate_molecule(self, molecule: dict[str, Any]) -> dict[str, Any]:
        return molecule

    def annotate_molecules(self, molecules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return molecules


def test_orchestrator_runs_agent_pipeline_and_writes_artifacts(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    result = orchestrator.rank("Parkinson disease", top=2)

    assert result.disease.canonical_name == "Parkinson disease"
    assert [candidate.name for candidate in result.candidates] == ["Levodopa", "Rasagiline"]
    assert [trace.agent_name for trace in result.traces] == [
        "DiseaseResolverAgent",
        "TargetDiscoveryAgent",
        "MoleculeRetrievalAgent",
        "NovelMoleculeAgent",
        "EvidenceScoringAgent",
        "ReportWriterAgent",
    ]

    output_dir = tmp_path / "parkinson-disease"
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "trace.json").exists()

    report = (output_dir / "report.md").read_text()
    assert "requires experimental validation" in report.lower()
    assert "medical advice" in report.lower()
    assert "does not predict" in report.lower()

    payload = json.loads((output_dir / "candidates.json").read_text())
    assert payload["candidates"][0]["score_breakdown"]["final_score"] > 0


def test_orchestrator_accepts_top_n_output_dir_and_runtime_config(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path / "unused"),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    result = orchestrator.rank(
        "Parkinson disease",
        top_n=1,
        output_dir=tmp_path / "custom-results",
        config={"limit_per_target": 5},
    )

    assert len(result.candidates) == 1
    assert (tmp_path / "custom-results" / "parkinson-disease" / "report.md").exists()


def test_disease_resolution_failure_stops_pipeline(tmp_path):
    target_source = FakeTargetSource()
    molecule_source = FakeMoleculeSource()
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FailingDiseaseSource(),
        target_source=target_source,
        molecule_source=molecule_source,
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    with pytest.raises(DiseaseResolutionError):
        orchestrator.rank("Unknown disease", top_n=2)

    assert target_source.calls == 0
    assert molecule_source.calls == 0
    assert not (tmp_path / "unknown-disease" / "report.md").exists()


def test_target_discovery_failure_stops_pipeline(tmp_path):
    molecule_source = FakeMoleculeSource()
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FailingTargetSource(),
        molecule_source=molecule_source,
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    with pytest.raises(TargetDiscoveryError):
        orchestrator.rank("Parkinson disease", top_n=2)

    assert molecule_source.calls == 0
    assert not (tmp_path / "parkinson-disease" / "report.md").exists()


def test_molecule_retrieval_failure_stops_pipeline(tmp_path):
    molecule_source = FailingMoleculeSource()
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=molecule_source,
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    with pytest.raises(MoleculeRetrievalError):
        orchestrator.rank("Parkinson disease", top_n=2)

    assert molecule_source.calls == 1
    assert not (tmp_path / "parkinson-disease" / "report.md").exists()


def test_no_candidates_found_stops_pipeline(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=EmptyMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    with pytest.raises(NoCandidatesFoundError):
        orchestrator.rank("Parkinson disease", top_n=2)

    assert not (tmp_path / "parkinson-disease" / "report.md").exists()


def test_orchestrator_failed_run_does_not_write_success_artifacts(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=EmptyTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
    )

    with pytest.raises(TargetDiscoveryError):
        orchestrator.rank("Parkinson disease", top=2)

    output_dir = tmp_path / "parkinson-disease"
    assert not (output_dir / "candidates.json").exists()
    assert not (output_dir / "report.md").exists()
    assert not (output_dir / "trace.json").exists()
