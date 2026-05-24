from __future__ import annotations

from typing import Any

import pytest
import requests

from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    EvidenceRetrievalError,
    ExternalDataUnavailableError,
    NoCandidatesFoundError,
)
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.data_sources.pubchem_adapter import PubChemAdapter
from molecule_ranker.schemas import Disease, Target


class MockResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class QueueSession:
    def __init__(self, responses: list[MockResponse] | None = None, error: Exception | None = None):
        self.responses = responses or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0)


def test_opentargets_resolves_disease_and_adds_real_source_provenance():
    session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "search": {
                            "hits": [
                                {
                                    "id": "MONDO_0005180",
                                    "name": "Parkinson disease",
                                    "entity": "disease",
                                }
                            ]
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "id": "MONDO_0005180",
                            "name": "Parkinson disease",
                            "description": "Mock description.",
                            "dbXRefs": ["UMLS:C0030567", "Orphanet:319705"],
                            "synonyms": [
                                {
                                    "terms": ["PD", "Parkinson's disease"],
                                    "relation": "hasExactSynonym",
                                }
                            ],
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "associatedTargets": {
                                "rows": [
                                    {
                                        "score": 0.88,
                                        "target": {
                                            "id": "ENSG000001",
                                            "approvedSymbol": "LRRK2",
                                            "approvedName": "LRRK2 kinase",
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            ),
        ]
    )
    adapter = OpenTargetsAdapter(session=session)  # type: ignore[arg-type]

    disease = adapter.resolve_disease("Parkinson disease")
    targets = adapter.discover_targets(disease, limit=1)

    assert disease.identifiers["open_targets"] == "MONDO_0005180"
    assert disease.identifiers["mondo"] == "MONDO:0005180"
    assert disease.identifiers["umls"] == "UMLS:C0030567"
    assert disease.identifiers["orphanet"] == "Orphanet:319705"
    assert disease.synonyms == ["PD", "Parkinson's disease"]
    assert targets[0].symbol == "LRRK2"
    assert targets[0].evidence[0].source == "Open Targets"
    assert targets[0].evidence[0].source_record_id == "MONDO_0005180:ENSG000001"
    assert targets[0].evidence[0].retrieval_timestamp is not None


def test_opentargets_failures_raise_domain_errors():
    no_hits = QueueSession([MockResponse({"data": {"search": {"hits": []}}})])
    with pytest.raises(DiseaseResolutionError):
        OpenTargetsAdapter(session=no_hits).resolve_disease("unknown")  # type: ignore[arg-type]

    failed = QueueSession(error=requests.Timeout("timeout"))
    with pytest.raises(ExternalDataUnavailableError):
        OpenTargetsAdapter(session=failed).resolve_disease("Parkinson disease")  # type: ignore[arg-type]


def test_chembl_retrieves_molecule_records_with_provenance():
    session = QueueSession(
        [
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL6152",
                            "organism": "Homo sapiens",
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "mechanisms": [
                        {
                            "mec_id": 123,
                            "molecule_chembl_id": "CHEMBL1",
                            "mechanism_of_action": "Target inhibitor",
                            "action_type": "INHIBITOR",
                            "direct_interaction": 1,
                            "max_phase": 2,
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "molecules": [
                        {"pref_name": "Mock molecule", "molecule_type": "Small molecule"}
                    ]
                }
            ),
        ]
    )
    adapter = ChEMBLAdapter(session=session)  # type: ignore[arg-type]
    disease = Disease(
        input_name="Disease",
        canonical_name="Disease",
        synonyms=[],
        identifiers={"open_targets": "MONDO_TEST"},
        description=None,
    )
    target = Target(
        symbol="SNCA",
        name="Alpha-synuclein",
        disease_relevance_score=0.8,
        evidence=[],
        mechanism=None,
    )

    records = adapter.retrieve_molecules(disease, [target], limit_per_target=1)

    assert records[0]["name"] == "Mock molecule"
    assert records[0]["evidence"][0]["source"] == "ChEMBL"
    assert records[0]["evidence"][0]["source_record_id"] == "123"


def test_chembl_retries_transient_server_errors():
    session = QueueSession(
        [
            MockResponse({}, status_code=500),
            MockResponse({"targets": []}),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        max_retries=1,
        retry_delay_seconds=0,
    )

    payload = adapter._get("target.json", {"limit": 1})

    assert payload == {"targets": []}
    assert len(session.calls) == 2


def test_chembl_preserves_mechanism_record_when_molecule_details_fail():
    session = QueueSession(
        [
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL6152",
                            "organism": "Homo sapiens",
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "mechanisms": [
                        {
                            "mec_id": 123,
                            "molecule_chembl_id": "CHEMBL1",
                            "mechanism_of_action": "Target inhibitor",
                            "action_type": "INHIBITOR",
                            "direct_interaction": 1,
                            "max_phase": 2,
                        }
                    ]
                }
            ),
            MockResponse({}, status_code=500),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )
    disease = Disease(
        input_name="Disease",
        canonical_name="Disease",
        synonyms=[],
        identifiers={"open_targets": "MONDO_TEST"},
        description=None,
    )
    target = Target(
        symbol="SNCA",
        name="Alpha-synuclein",
        disease_relevance_score=0.8,
        evidence=[],
        mechanism=None,
    )

    records = adapter.retrieve_molecules(disease, [target], limit_per_target=1)

    assert records[0]["name"] == "CHEMBL1"
    assert records[0]["evidence"][0]["source"] == "ChEMBL"
    assert "ChEMBL molecule detail unavailable" in records[0]["warnings"][0]


def test_chembl_no_records_raises_no_candidates():
    session = QueueSession([MockResponse({"targets": []})])
    adapter = ChEMBLAdapter(session=session)  # type: ignore[arg-type]
    disease = Disease(
        input_name="Disease",
        canonical_name="Disease",
        synonyms=[],
        identifiers={},
        description=None,
    )
    target = Target(
        symbol="NONE",
        name=None,
        disease_relevance_score=0.1,
        evidence=[],
        mechanism=None,
    )

    with pytest.raises(NoCandidatesFoundError):
        adapter.retrieve_molecules(disease, [target])


def test_pubchem_enriches_molecule_with_cid_and_metadata():
    session = QueueSession(
        [
            MockResponse({"IdentifierList": {"CID": [2244]}}),
            MockResponse({"InformationList": {"Information": [{"Synonym": ["aspirin"]}]}}),
            MockResponse(
                {
                    "PropertyTable": {
                        "Properties": [
                            {
                                "CID": 2244,
                                "MolecularFormula": "C9H8O4",
                                "MolecularWeight": 180.16,
                            }
                        ]
                    }
                }
            ),
        ]
    )
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    enriched = adapter.annotate_molecule({"name": "aspirin", "identifiers": {}})

    assert enriched["identifiers"]["pubchem_cid"] == "2244"
    assert enriched["evidence"][-1]["source"] == "PubChem"
    assert enriched["evidence"][-1]["source_record_id"] == "2244"


def test_pubchem_preserves_molecule_when_annotation_record_is_missing():
    session = QueueSession([MockResponse({}, status_code=404)])
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]
    molecule = {
        "name": "GANTENERUMAB",
        "identifiers": {"chembl": "CHEMBL1743025"},
        "evidence": [
            {
                "source": "ChEMBL",
                "source_record_id": "5455",
                "title": "Mechanism record",
                "evidence_type": "mechanism",
                "summary": "ChEMBL mechanism evidence.",
                "confidence": 0.8,
                "metadata": {},
            }
        ],
    }

    annotated = adapter.annotate_molecules([molecule])

    assert annotated[0]["identifiers"] == {"chembl": "CHEMBL1743025"}
    assert annotated[0]["evidence"] == molecule["evidence"]
    assert "PubChem returned no record" in annotated[0]["warnings"][0]


def test_pubchem_single_molecule_missing_record_raises_evidence_error():
    session = QueueSession([MockResponse({}, status_code=404)])
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    with pytest.raises(EvidenceRetrievalError):
        adapter.annotate_molecule({"name": "GANTENERUMAB", "identifiers": {}})
