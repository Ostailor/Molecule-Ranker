from __future__ import annotations

import json
from typing import Any

import pytest

from molecule_ranker.config import RankerConfig
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
    TargetDiscoveryError,
)
from molecule_ranker.generation.errors import GenerationError
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


class UnavailableDiseaseSource:
    def resolve_disease(self, disease_name: str) -> Disease:
        raise ExternalDataUnavailableError("Open Targets is unavailable.")


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


class EmptyLiteratureSource:
    source_name = "Test PubMed"

    def retrieve_papers(self, query: Any) -> list[Any]:
        return []


class FailingLiteratureSource:
    source_name = "Failing PubMed"

    def search(self, query: Any) -> list[Any]:
        raise ExternalDataUnavailableError("PubMed unavailable")


def test_orchestrator_runs_agent_pipeline_and_writes_artifacts(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    result = orchestrator.rank("Parkinson disease", top=2)

    assert result.disease.canonical_name == "Parkinson disease"
    assert [candidate.name for candidate in result.candidates] == ["Levodopa", "Rasagiline"]
    assert [trace.agent_name for trace in result.traces] == [
        "DiseaseResolverAgent",
        "TargetDiscoveryAgent",
        "MoleculeRetrievalAgent",
        "LiteratureEvidenceAgent",
        "NovelMoleculeAgent",
        "DevelopabilityAssessmentAgent",
        "ExperimentalEvidenceAgent",
        "PredictiveModelAgent",
        "EvidenceScoringAgent",
        "PortfolioOptimizationAgent",
        "CodexBackboneAgent",
        "ReviewWorkspaceAgent",
        "ReportWriterAgent",
    ]

    output_dir = tmp_path / "parkinson-disease"
    assert (output_dir / "candidates.json").exists()
    assert (output_dir / "report.md").exists()
    assert (output_dir / "developability_report.md").exists()
    assert (output_dir / "developability_assessments.json").exists()
    assert (output_dir / "trace.json").exists()
    assert not (output_dir / "generated_candidates.json").exists()
    assert not (output_dir / "generation_trace.json").exists()

    report = (output_dir / "report.md").read_text()
    assert "requires experimental validation" in report.lower()
    assert "medical advice" in report.lower()
    assert "does not predict" in report.lower()

    payload = json.loads((output_dir / "candidates.json").read_text())
    assert payload["candidates"][0]["score_breakdown"]["final_score"] > 0
    for candidate in result.candidates:
        assert candidate.score is not None
        assert 0 <= candidate.score <= 1
        assert candidate.score_breakdown is not None
        assert candidate.score_breakdown.explanation
        assert 0 <= candidate.score_breakdown.confidence <= 1


def test_orchestrator_writes_generation_artifacts_when_generation_enabled(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path, enable_generation=True),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    orchestrator.rank("Parkinson disease", top=2)

    output_dir = tmp_path / "parkinson-disease"
    assert (output_dir / "generated_candidates.json").exists()
    assert (output_dir / "generation_trace.json").exists()
    payload = json.loads((output_dir / "generated_candidates.json").read_text())
    assert payload["success"] is True
    assert payload["generation_enabled"] is True
    assert payload["warnings"]
    assert payload["retained_count"] == 0
    trace_payload = json.loads((output_dir / "generation_trace.json").read_text())
    assert "seed_selection_trace" in trace_payload
    assert trace_payload["generator_method"] == "generator_ensemble"


def test_strict_generation_failure_does_not_write_success_generation_files(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(
            results_dir=tmp_path,
            enable_generation=True,
            strict_generation=True,
        ),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    with pytest.raises(GenerationError):
        orchestrator.rank("Parkinson disease", top=2)

    output_dir = tmp_path / "parkinson-disease"
    assert not (output_dir / "generated_candidates.json").exists()
    assert not (output_dir / "generation_trace.json").exists()


def test_orchestrator_can_disable_literature_agent(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path, enable_literature=False),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=FailingLiteratureSource(),
    )

    result = orchestrator.rank("Parkinson disease", top=1)

    assert "LiteratureEvidenceAgent" not in [trace.agent_name for trace in result.traces]
    assert result.candidates[0].score is not None


def test_orchestrator_default_literature_failure_continues_with_warning(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path, strict_literature=False),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=FailingLiteratureSource(),
    )

    result = orchestrator.rank("Parkinson disease", top=1)

    trace = next(trace for trace in result.traces if trace.agent_name == "LiteratureEvidenceAgent")
    assert result.candidates[0].score is not None
    assert trace.metadata["failures"]


def test_orchestrator_strict_literature_failure_stops_pipeline(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path, strict_literature=True),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=FailingLiteratureSource(),
    )

    with pytest.raises(ExternalDataUnavailableError, match="PubMed unavailable"):
        orchestrator.rank("Parkinson disease", top=1)


def test_orchestrator_accepts_top_n_output_dir_and_runtime_config(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path / "unused"),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    result = orchestrator.rank(
        "Parkinson disease",
        top_n=1,
        output_dir=tmp_path / "custom-results",
        config={"limit_per_target": 5},
    )

    assert len(result.candidates) == 1
    assert (tmp_path / "custom-results" / "parkinson-disease" / "report.md").exists()


def test_orchestrator_writes_effective_config_to_trace_metadata(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(
            results_dir=tmp_path,
            default_target_limit=7,
            target_source_limit=35,
            max_molecules_per_target=4,
            max_activity_records_per_target=9,
            allow_cached_real_data=True,
            enable_novel_generation=True,
            generated_candidate_limit=3,
            generation_attempt_budget=40,
            generation_random_seed=11,
        ),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    orchestrator.rank("Parkinson disease", top_n=1)

    trace_payload = json.loads((tmp_path / "parkinson-disease" / "trace.json").read_text())
    config_payload = trace_payload["config"]
    assert config_payload["default_target_limit"] == 7
    assert config_payload["target_source_limit"] == 35
    assert config_payload["max_molecules_per_target"] == 4
    assert config_payload["max_activity_records_per_target"] == 9
    assert config_payload["allow_cached_real_data"] is True
    assert config_payload["enable_novel_generation"] is True
    assert config_payload["generated_candidate_limit"] == 3
    assert config_payload["generation_attempt_budget"] == 40
    assert config_payload["generation_random_seed"] == 11


def test_disease_resolution_failure_stops_pipeline(tmp_path):
    target_source = FakeTargetSource()
    molecule_source = FakeMoleculeSource()
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FailingDiseaseSource(),
        target_source=target_source,
        molecule_source=molecule_source,
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    with pytest.raises(DiseaseResolutionError):
        orchestrator.rank("Unknown disease", top_n=2)

    assert target_source.calls == 0
    assert molecule_source.calls == 0
    assert not (tmp_path / "unknown-disease" / "report.md").exists()


def test_external_data_unavailable_stops_pipeline(tmp_path):
    target_source = FakeTargetSource()
    molecule_source = FakeMoleculeSource()
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=UnavailableDiseaseSource(),
        target_source=target_source,
        molecule_source=molecule_source,
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
    )

    with pytest.raises(ExternalDataUnavailableError):
        orchestrator.rank("Parkinson disease", top_n=2)

    assert target_source.calls == 0
    assert molecule_source.calls == 0
    assert not (tmp_path / "parkinson-disease" / "report.md").exists()


def test_target_discovery_failure_stops_pipeline(tmp_path):
    molecule_source = FakeMoleculeSource()
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FailingTargetSource(),
        molecule_source=molecule_source,
        molecule_annotation_source=NoOpAnnotationSource(),
        literature_source=EmptyLiteratureSource(),
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
        literature_source=EmptyLiteratureSource(),
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
        literature_source=EmptyLiteratureSource(),
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
        literature_source=EmptyLiteratureSource(),
    )

    with pytest.raises(TargetDiscoveryError):
        orchestrator.rank("Parkinson disease", top=2)

    output_dir = tmp_path / "parkinson-disease"
    assert not (output_dir / "candidates.json").exists()
    assert not (output_dir / "report.md").exists()
    assert not (output_dir / "trace.json").exists()
