from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.molecule_retrieval import MoleculeRetrievalAgent
from molecule_ranker.data_sources.errors import (
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
)
from molecule_ranker.schemas import Disease, EvidenceItem, Target


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        synonyms=[],
        identifiers={"open_targets": "MONDO_0005180"},
        description=None,
    )


def _target(symbol: str) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} target",
        disease_relevance_score=0.8,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"MONDO_0005180:{symbol}",
                title="Target association",
                evidence_type="target_disease_association",
                summary="Mocked target association.",
                confidence=0.8,
                metadata={"query": "test"},
            )
        ],
        mechanism=None,
    )


def _evidence(record_id: str, target: str) -> dict[str, Any]:
    return EvidenceItem(
        source="ChEMBL",
        source_record_id=record_id,
        title=f"Mechanism record for {target}",
        evidence_type="mechanism",
        summary=f"Mocked ChEMBL mechanism for {target}.",
        confidence=0.8,
        metadata={"target": target},
    ).model_dump(mode="json")


class DuplicateMoleculeSource:
    source_name = "ChEMBL"

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": "Shared molecule",
                "molecule_type": "small_molecule",
                "identifiers": {"chembl": "CHEMBL1"},
                "known_targets": ["LRRK2"],
                "development_status": "max_phase_2",
                "mechanism_of_action": "Target interaction.",
                "target_fit": 0.8,
                "clinical_precedence": 0.5,
                "safety_prior": 0.5,
                "repurposing_value": 0.5,
                "evidence": [_evidence("mec-1", "LRRK2")],
            },
            {
                "name": "Shared molecule",
                "molecule_type": "small_molecule",
                "identifiers": {"chembl": "CHEMBL1"},
                "known_targets": ["SNCA"],
                "development_status": "max_phase_2",
                "mechanism_of_action": "Target interaction.",
                "target_fit": 0.7,
                "clinical_precedence": 0.5,
                "safety_prior": 0.5,
                "repurposing_value": 0.5,
                "evidence": [_evidence("mec-2", "SNCA")],
            },
            {
                "name": "No evidence molecule",
                "molecule_type": "small_molecule",
                "identifiers": {"chembl": "CHEMBL2"},
                "known_targets": ["SNCA"],
                "evidence": [],
            },
        ]


class PubChemAnnotationSource:
    source_name = "PubChem"

    def annotate_molecules(self, molecules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        annotated = []
        for molecule in molecules:
            enriched = dict(molecule)
            identifiers = dict(enriched.get("identifiers", {}))
            identifiers.setdefault("pubchem_cid", "123")
            identifiers.setdefault("inchikey", "TEST-INCHIKEY")
            enriched["identifiers"] = identifiers
            enriched["chemical_metadata"] = {"canonical_smiles": "CCO"}
            annotated.append(enriched)
        return annotated

    def annotate_molecule(self, molecule: dict[str, Any]) -> dict[str, Any]:
        return self.annotate_molecules([molecule])[0]


class PassthroughAnnotationSource:
    source_name = "PubChem"

    def annotate_molecules(self, molecules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return molecules

    def annotate_molecule(self, molecule: dict[str, Any]) -> dict[str, Any]:
        return molecule


class EmptyMoleculeSource:
    source_name = "ChEMBL"

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        return []


class UnavailableMoleculeSource:
    source_name = "ChEMBL"

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        raise ExternalDataUnavailableError("ChEMBL unavailable")


class StaticMoleculeSource:
    source_name = "ChEMBL"

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def retrieve_molecules(
        self, disease: Disease, targets: list[Target], *, limit_per_target: int = 10
    ) -> list[dict[str, Any]]:
        return self.records


def _record(
    *,
    name: str,
    identifiers: dict[str, str],
    target: str,
    evidence_id: str,
    evidence_source: str = "ChEMBL",
    development_status: str | None = "max_phase_1",
    chemical_metadata: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    evidence = EvidenceItem(
        source=evidence_source,
        source_record_id=evidence_id,
        title=f"{evidence_source} record",
        evidence_type="mechanism",
        summary="Retrieved molecule-target evidence.",
        confidence=0.8,
        metadata={"response_provenance": {"mode": "live", "source": evidence_source}},
    ).model_dump(mode="json")
    return {
        "name": name,
        "molecule_type": "small_molecule",
        "identifiers": identifiers,
        "known_targets": [target],
        "development_status": development_status,
        "mechanism_of_action": "Target interaction.",
        "chemical_metadata": chemical_metadata or {},
        "warnings": warnings or [],
        "evidence": [evidence],
    }


def test_molecule_retrieval_deduplicates_merges_evidence_and_sets_candidates():
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target("LRRK2"), _target("SNCA")],
    )

    result = MoleculeRetrievalAgent(
        DuplicateMoleculeSource(),
        PubChemAnnotationSource(),
    ).run(context)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "Shared molecule"
    assert candidate.identifiers["chembl"] == "CHEMBL1"
    assert candidate.identifiers["pubchem_cid"] == "123"
    assert candidate.chemical_metadata["canonical_smiles"] == "CCO"
    assert sorted(candidate.known_targets) == ["LRRK2", "SNCA"]
    assert [item.source_record_id for item in candidate.evidence] == ["mec-1", "mec-2"]
    trace = result.traces[-1]
    assert trace.metadata["targets_queried"] == 2
    assert trace.metadata["sources_used"] == ["ChEMBL", "PubChem"]
    assert trace.metadata["raw_molecule_records"] == 3
    assert trace.metadata["deduplicated_molecules"] == 1
    assert trace.metadata["deduplication_identifiers"] == ["inchikey:TEST-INCHIKEY"]
    assert trace.metadata["merge_count"] == 1
    assert trace.metadata["duplicate_keys_seen"] == [
        "inchikey:TEST-INCHIKEY",
        "inchikey:TEST-INCHIKEY",
    ]
    assert trace.metadata["deduplication_method"] == {
        "inchikey:TEST-INCHIKEY": "inchikey"
    }


def test_molecule_retrieval_deduplicates_by_chembl_parent_id_first():
    source = StaticMoleculeSource(
        [
            _record(
                name="Parent form A",
                identifiers={
                    "chembl": "CHEMBL_CHILD_A",
                    "chembl_parent_id": "CHEMBL_PARENT",
                    "inchikey": "AAAA",
                },
                target="LRRK2",
                evidence_id="mec-parent-a",
                development_status="max_phase_1",
                chemical_metadata={"canonical_smiles": "CCO"},
            ),
            _record(
                name="Parent form B",
                identifiers={
                    "chembl": "CHEMBL_CHILD_B",
                    "chembl_parent_id": "CHEMBL_PARENT",
                    "inchikey": "BBBB",
                },
                target="SNCA",
                evidence_id="mec-parent-b",
                development_status="max_phase_3",
                chemical_metadata={"canonical_smiles": "CCN"},
            ),
        ]
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target("LRRK2"), _target("SNCA")],
    )

    result = MoleculeRetrievalAgent(source, PassthroughAnnotationSource()).run(context)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert sorted(candidate.known_targets) == ["LRRK2", "SNCA"]
    assert {item.source_record_id for item in candidate.evidence} == {
        "mec-parent-a",
        "mec-parent-b",
    }
    assert candidate.identifiers["chembl_parent_id"] == "CHEMBL_PARENT"
    assert candidate.development_status == "max_phase_3"
    assert any("Conflicting chemical metadata" in warning for warning in candidate.warnings)
    trace = result.traces[-1]
    assert trace.metadata["deduplication_identifiers"] == [
        "chembl_parent:CHEMBL_PARENT"
    ]
    assert trace.metadata["deduplication_method"] == {
        "chembl_parent:CHEMBL_PARENT": "chembl_parent"
    }
    assert trace.metadata["merge_sources"] == ["ChEMBL"]


def test_molecule_retrieval_deduplicates_by_inchikey_and_source_record_id_pair():
    source = StaticMoleculeSource(
        [
            _record(
                name="InChIKey record A",
                identifiers={"chembl": "CHEMBL_A", "inchikey": "SAME-INCHIKEY"},
                target="LRRK2",
                evidence_id="shared-record",
                evidence_source="ChEMBL",
            ),
            _record(
                name="InChIKey record B",
                identifiers={"pubchem_cid": "123", "inchikey": "SAME-INCHIKEY"},
                target="SNCA",
                evidence_id="shared-record",
                evidence_source="ChEMBL",
            ),
            _record(
                name="InChIKey record C",
                identifiers={"inchikey": "SAME-INCHIKEY"},
                target="SNCA",
                evidence_id="shared-record",
                evidence_source="PubChem",
            ),
        ]
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target("LRRK2"), _target("SNCA")],
    )

    result = MoleculeRetrievalAgent(source, PassthroughAnnotationSource()).run(context)

    candidate = result.candidates[0]
    evidence_keys = [
        (item.source, item.source_record_id)
        for item in candidate.evidence
    ]
    assert evidence_keys == [
        ("ChEMBL", "shared-record"),
        ("PubChem", "shared-record"),
    ]
    assert sorted(candidate.known_targets) == ["LRRK2", "SNCA"]
    assert candidate.identifiers["chembl"] == "CHEMBL_A"
    assert candidate.identifiers["pubchem_cid"] == "123"
    trace = result.traces[-1]
    assert trace.metadata["merge_count"] == 2
    assert trace.metadata["merge_sources"] == ["ChEMBL", "PubChem"]


def test_molecule_retrieval_name_only_dedup_adds_low_confidence_warning():
    source = StaticMoleculeSource(
        [
            _record(
                name="Same Name",
                identifiers={},
                target="LRRK2",
                evidence_id="name-a",
            ),
            _record(
                name="Same Name",
                identifiers={},
                target="SNCA",
                evidence_id="name-b",
            ),
        ]
    )
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target("LRRK2"), _target("SNCA")],
    )

    result = MoleculeRetrievalAgent(source, PassthroughAnnotationSource()).run(context)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert any("normalized-name-only deduplication" in warning for warning in candidate.warnings)
    trace = result.traces[-1]
    assert trace.metadata["deduplication_identifiers"] == ["name:same-name"]
    assert trace.metadata["deduplication_method"] == {"name:same-name": "name"}


def test_molecule_retrieval_requires_targets():
    context = PipelineContext(disease_input="Parkinson disease", disease=_disease())

    with pytest.raises(MoleculeRetrievalError):
        MoleculeRetrievalAgent(DuplicateMoleculeSource()).run(context)

    assert context.traces[-1].warnings


def test_molecule_retrieval_fails_when_no_records_returned():
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target("LRRK2")],
    )

    with pytest.raises(NoCandidatesFoundError):
        MoleculeRetrievalAgent(EmptyMoleculeSource()).run(context)

    assert context.traces[-1].warnings


def test_molecule_retrieval_propagates_external_api_failure():
    context = PipelineContext(
        disease_input="Parkinson disease",
        disease=_disease(),
        targets=[_target("LRRK2")],
    )

    with pytest.raises(ExternalDataUnavailableError):
        MoleculeRetrievalAgent(UnavailableMoleculeSource()).run(context)

    assert context.traces[-1].warnings
