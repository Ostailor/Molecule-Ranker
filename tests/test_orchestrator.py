from __future__ import annotations

import json
from typing import Any

from molecule_ranker.config import RankerConfig
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


class FakeTargetSource:
    def discover_targets(self, disease: Disease, *, limit: int = 20) -> list[Target]:
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


class FakeMoleculeSource:
    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
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


def test_orchestrator_runs_agent_pipeline_and_writes_artifacts(tmp_path):
    orchestrator = MoleculeRankerOrchestrator(
        config=RankerConfig(results_dir=tmp_path),
        disease_source=FakeDiseaseSource(),
        target_source=FakeTargetSource(),
        molecule_source=FakeMoleculeSource(),
    )

    result = orchestrator.rank("Parkinson disease", top=2)

    assert result.disease.canonical_name == "Parkinson disease"
    assert [candidate.name for candidate in result.candidates] == ["Levodopa", "Rasagiline"]
    assert [trace.agent_name for trace in result.traces] == [
        "DiseaseResolverAgent",
        "TargetDiscoveryAgent",
        "MoleculeRetrievalAgent",
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
    assert "cure" not in report.lower()

    payload = json.loads((output_dir / "candidates.json").read_text())
    assert payload["candidates"][0]["score_breakdown"]["final_score"] > 0
