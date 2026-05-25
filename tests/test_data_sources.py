from __future__ import annotations

from typing import Any

import pytest
import requests

from molecule_ranker.data_sources.chembl_adapter import ChEMBLAdapter
from molecule_ranker.data_sources.chembl_target_mapper import ChEMBLTargetMapping
from molecule_ranker.data_sources.errors import (
    DiseaseResolutionError,
    EvidenceRetrievalError,
    ExternalDataUnavailableError,
    MoleculeRetrievalError,
    NoCandidatesFoundError,
)
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.data_sources.pubchem_adapter import PubChemAdapter
from molecule_ranker.schemas import Disease, Target
from molecule_ranker.utils.http_cache import HttpResponseCache


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
    metadata = adapter.last_resolution_metadata
    assert metadata["search_hit_count"] == 1
    assert metadata["selected_disease_id"] == "MONDO_0005180"
    assert metadata["selected_disease_name"] == "Parkinson disease"
    assert metadata["match_reason"] == "exact_canonical_match"
    assert metadata["ambiguity"] is False


def test_opentargets_accepts_exact_synonym_match_from_real_source_details():
    session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "search": {
                            "hits": [
                                {
                                    "id": "MONDO_0004975",
                                    "name": "Alzheimer disease",
                                    "entity": "disease",
                                    "score": 0.8,
                                },
                                {
                                    "id": "EFO_0000249",
                                    "name": "dementia",
                                    "entity": "disease",
                                    "score": 0.7,
                                },
                            ]
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "id": "MONDO_0004975",
                            "name": "Alzheimer disease",
                            "description": "Neurodegenerative disease.",
                            "dbXRefs": ["EFO:0000249"],
                            "synonyms": [
                                {
                                    "terms": ["AD", "Alzheimer's disease"],
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
                            "id": "EFO_0000249",
                            "name": "dementia",
                            "description": "Dementia.",
                            "dbXRefs": [],
                            "synonyms": [{"terms": [], "relation": "hasExactSynonym"}],
                        }
                    }
                }
            ),
        ]
    )
    adapter = OpenTargetsAdapter(session=session)  # type: ignore[arg-type]

    disease = adapter.resolve_disease("AD")

    assert disease.canonical_name == "Alzheimer disease"
    assert disease.identifiers["open_targets"] == "MONDO_0004975"
    assert adapter.last_resolution_metadata["match_reason"] == "exact_synonym_match"
    assert adapter.last_resolution_metadata["search_hit_count"] == 2
    assert adapter.last_resolution_metadata["ambiguity"] is False


def test_opentargets_accepts_high_confidence_top_match_with_margin():
    session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "search": {
                            "hits": [
                                {
                                    "id": "MONDO_TOP",
                                    "name": "Top disease",
                                    "entity": "disease",
                                    "score": 0.92,
                                },
                                {
                                    "id": "MONDO_SECOND",
                                    "name": "Second disease",
                                    "entity": "disease",
                                    "score": 0.55,
                                },
                            ]
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "id": "MONDO_TOP",
                            "name": "Top disease",
                            "description": "Top.",
                            "dbXRefs": [],
                            "synonyms": [],
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "id": "MONDO_SECOND",
                            "name": "Second disease",
                            "description": "Second.",
                            "dbXRefs": [],
                            "synonyms": [],
                        }
                    }
                }
            ),
        ]
    )
    adapter = OpenTargetsAdapter(session=session)  # type: ignore[arg-type]

    disease = adapter.resolve_disease("top-ish")

    assert disease.identifiers["open_targets"] == "MONDO_TOP"
    assert adapter.last_resolution_metadata["match_reason"] == "high_confidence_margin"


def test_opentargets_failures_raise_domain_errors():
    no_hits = QueueSession([MockResponse({"data": {"search": {"hits": []}}})])
    with pytest.raises(DiseaseResolutionError):
        OpenTargetsAdapter(session=no_hits).resolve_disease("unknown")  # type: ignore[arg-type]

    failed = QueueSession(error=requests.Timeout("timeout"))
    with pytest.raises(ExternalDataUnavailableError):
        OpenTargetsAdapter(session=failed).resolve_disease("Parkinson disease")  # type: ignore[arg-type]


def test_opentargets_rejects_ambiguous_exact_disease_matches():
    session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "search": {
                            "hits": [
                                {"id": "EFO_1", "name": "Disease X", "entity": "disease"},
                                {"id": "MONDO_2", "name": "Disease X", "entity": "disease"},
                            ]
                        }
                    }
                }
            )
        ]
    )

    with pytest.raises(DiseaseResolutionError, match="Disease input was ambiguous"):
        OpenTargetsAdapter(session=session).resolve_disease("Disease X")  # type: ignore[arg-type]

    assert session.calls[0]["json"]["variables"]["size"] == 10


def test_opentargets_rejects_ambiguous_close_scored_matches_with_candidates():
    session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "search": {
                            "hits": [
                                {
                                    "id": "MONDO_1",
                                    "name": "Alpha condition",
                                    "entity": "disease",
                                    "score": 0.81,
                                },
                                {
                                    "id": "MONDO_2",
                                    "name": "Beta condition",
                                    "entity": "disease",
                                    "score": 0.78,
                                },
                            ]
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "id": "MONDO_1",
                            "name": "Alpha condition",
                            "description": "Alpha.",
                            "dbXRefs": [],
                            "synonyms": [],
                        }
                    }
                }
            ),
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "id": "MONDO_2",
                            "name": "Beta condition",
                            "description": "Beta.",
                            "dbXRefs": [],
                            "synonyms": [],
                        }
                    }
                }
            ),
        ]
    )

    with pytest.raises(DiseaseResolutionError) as error:
        OpenTargetsAdapter(session=session).resolve_disease("condition")  # type: ignore[arg-type]

    message = str(error.value)
    assert "Disease input was ambiguous. Top matches:" in message
    assert "Alpha condition" in message
    assert "Beta condition" in message


def test_opentargets_adds_richer_target_metadata_and_identifiers():
    session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "associatedTargets": {
                                "rows": [
                                    {
                                        "score": 0.91,
                                        "target": {
                                            "id": "ENSG000001",
                                            "approvedSymbol": "LRRK2",
                                            "approvedName": "leucine rich repeat kinase 2",
                                            "biotype": "protein_coding",
                                            "proteinIds": [
                                                {"id": "Q5S007", "source": "uniprot_swissprot"}
                                            ],
                                            "tractability": [
                                                {
                                                    "label": "Small molecule",
                                                    "modality": "SM",
                                                    "value": True,
                                                }
                                            ],
                                            "safetyLiabilities": [
                                                {
                                                    "event": "central nervous system toxicity",
                                                    "effects": [{"direction": "activation"}],
                                                }
                                            ],
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            )
        ]
    )
    adapter = OpenTargetsAdapter(session=session)  # type: ignore[arg-type]
    disease = Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"open_targets": "MONDO_0005180"},
    )

    targets = adapter.discover_targets(disease, limit=1)

    assert targets[0].identifiers["ensembl"] == "ENSG000001"
    assert targets[0].identifiers["uniprot"] == "Q5S007"
    assert targets[0].target_class == "protein_coding"
    assert targets[0].tractability[0]["label"] == "Small molecule"
    assert targets[0].safety[0]["event"] == "central nervous system toxicity"
    assert targets[0].metadata["tractability"][0]["label"] == "Small molecule"
    assert targets[0].metadata["safety_liabilities"][0]["event"] == (
        "central nervous system toxicity"
    )
    assert targets[0].metadata["association_score"] == 0.91
    assert targets[0].metadata["approved_symbol"] == "LRRK2"
    assert targets[0].metadata["approved_name"] == "leucine rich repeat kinase 2"
    assert targets[0].evidence[0].metadata["target_metadata"]["biotype"] == "protein_coding"
    assert targets[0].evidence[0].metadata["association_score"] == 0.91
    assert targets[0].evidence[0].source_record_id == "MONDO_0005180:ENSG000001"
    assert targets[0].evidence[0].retrieval_timestamp is not None


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


def test_chembl_retrieves_mechanisms_activities_assays_indications_and_warnings():
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
                            "mechanism_of_action": "LRRK2 inhibitor",
                            "action_type": "INHIBITOR",
                            "direct_interaction": 1,
                            "max_phase": 3,
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "molecules": [
                        {"pref_name": "Mechanism molecule", "molecule_type": "Small molecule"}
                    ]
                }
            ),
            MockResponse(
                {
                    "drug_indications": [
                        {
                            "molecule_chembl_id": "CHEMBL1",
                            "drugind_id": 1001,
                            "mesh_heading": "Parkinson Disease",
                            "mesh_id": "D010300",
                            "efo_id": "EFO_0002508",
                            "max_phase_for_ind": 3,
                            "ref_type": "ClinicalTrials",
                            "ref_id": "NCT00000001",
                            "ref_url": "https://clinicaltrials.gov/study/NCT00000001",
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "drug_warnings": [
                        {
                            "molecule_chembl_id": "CHEMBL1",
                            "warning_id": 9001,
                            "warning_type": "Black Box Warning",
                            "warning_description": "Serious warning.",
                            "warning_class": "boxed_warning",
                            "warning_country": "US",
                            "warning_year": 2020,
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": 456,
                            "molecule_chembl_id": "CHEMBL2",
                            "assay_chembl_id": "CHEMBL_A1",
                            "pchembl_value": "7.2",
                            "standard_type": "IC50",
                            "standard_value": "15",
                            "standard_units": "nM",
                            "target_chembl_id": "CHEMBL6152",
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "molecules": [
                        {"pref_name": "Activity molecule", "molecule_type": "Small molecule"}
                    ]
                }
            ),
            MockResponse(
                {
                    "assays": [
                        {
                            "assay_chembl_id": "CHEMBL_A1",
                            "description": "Binding assay",
                            "assay_type": "B",
                        }
                    ]
                }
            ),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )
    disease = Disease(input_name="Disease", canonical_name="Disease")
    target = Target(
        symbol="LRRK2",
        name="LRRK2",
        identifiers={"uniprot": "Q5S007"},
        disease_relevance_score=0.8,
    )

    records = adapter.retrieve_molecules(disease, [target], limit_per_target=1)

    assert session.calls[0]["params"]["target_components__accession"] == "Q5S007"
    evidence_types = {
        item["evidence_type"]
        for record in records
        for item in record["evidence"]
    }
    assert {"mechanism", "activity", "assay", "indication", "safety_warning"} <= evidence_types
    mechanism_record = next(
        record for record in records if record["identifiers"]["chembl"] == "CHEMBL1"
    )
    assert mechanism_record["development_status"] == "max_phase_3"
    assert mechanism_record["safety_prior"] < 0.5
    indication = next(
        item for item in mechanism_record["evidence"] if item["evidence_type"] == "indication"
    )
    assert indication["source_record_id"] == "1001"
    assert indication["metadata"]["mesh_id"] == "D010300"
    assert indication["metadata"]["efo_id"] == "EFO_0002508"
    assert indication["metadata"]["max_phase_for_ind"] == 3.0
    assert indication["metadata"]["query_disease_match"] is False
    assert indication["metadata"]["reference_info"]["ref_id"] == "NCT00000001"
    safety_warning = next(
        item
        for item in mechanism_record["evidence"]
        if item["evidence_type"] == "safety_warning"
    )
    assert safety_warning["source_record_id"] == "9001"
    assert safety_warning["metadata"]["warning_type"] == "Black Box Warning"
    assert safety_warning["metadata"]["country"] == "US"
    assert safety_warning["metadata"]["year"] == 2020
    assert safety_warning["metadata"]["warning_class"] == "boxed_warning"
    activity_record = next(
        record for record in records if record["identifiers"]["chembl"] == "CHEMBL2"
    )
    assert activity_record["target_fit"] > 0.8


def test_chembl_activity_uses_molecule_max_phase_for_clinical_precedence():
    session = QueueSession(
        [
            MockResponse({"mechanisms": []}),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "ACT1",
                            "molecule_chembl_id": "CHEMBL_PHASE",
                            "assay_chembl_id": "ASSAY1",
                            "standard_type": "IC50",
                            "standard_value": "10",
                        }
                    ]
                }
            ),
            MockResponse(
                {
                    "molecules": [
                        {
                            "pref_name": "Clinical activity molecule",
                            "molecule_type": "Small molecule",
                            "max_phase": 4,
                        }
                    ]
                }
            ),
            MockResponse({"assays": [{"assay_chembl_id": "ASSAY1", "confidence_score": 8}]}),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        target_mapper=StaticTargetMapper({"LRRK2": _chembl_mapping("LRRK2", "CHEMBL_T1")}),  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    records = adapter.retrieve_molecules(
        Disease(input_name="Disease", canonical_name="Disease"),
        [Target(symbol="LRRK2", disease_relevance_score=0.8)],
    )

    assert records[0]["clinical_precedence"] == 1.0
    assert records[0]["development_status"] == "molecule_max_phase_4"


def test_chembl_activity_records_are_paginated_normalized_and_provenanced():
    session = QueueSession(
        [
            MockResponse({"mechanisms": []}),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "ACT1",
                            "molecule_chembl_id": "CHEMBL_A",
                            "assay_chembl_id": "ASSAY1",
                            "pchembl_value": "8.1",
                            "standard_type": "ic50",
                            "standard_value": "12.5",
                            "standard_units": "nM",
                            "standard_relation": "=",
                            "target_chembl_id": "CHEMBL_T1",
                        }
                    ],
                    "page_meta": {"next": "activity.json?offset=2"},
                }
            ),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "ACT2",
                            "molecule_chembl_id": "CHEMBL_B",
                            "assay_chembl_id": "ASSAY2",
                            "pchembl_value": "6.5",
                            "standard_type": "Ki",
                            "standard_value": "220",
                            "standard_units": "nM",
                            "standard_relation": "<",
                            "target_chembl_id": "CHEMBL_T1",
                        }
                    ],
                    "page_meta": {"next": None},
                }
            ),
            MockResponse({"molecules": [{"pref_name": "Activity A"}]}),
            MockResponse(
                {
                    "assays": [
                        {
                            "assay_chembl_id": "ASSAY1",
                            "description": "Binding assay",
                            "assay_type": "B",
                            "confidence_score": 9,
                        }
                    ]
                }
            ),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
            MockResponse({"molecules": [{"pref_name": "Activity B"}]}),
            MockResponse(
                {
                    "assays": [
                        {
                            "assay_chembl_id": "ASSAY2",
                            "description": "Functional assay",
                            "assay_type": "F",
                            "confidence_score": 7,
                        }
                    ]
                }
            ),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
        ]
    )
    mapper = StaticTargetMapper({"LRRK2": _chembl_mapping("LRRK2", "CHEMBL_T1")})
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        target_mapper=mapper,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    records = adapter.retrieve_molecules(
        Disease(input_name="Disease", canonical_name="Disease"),
        [Target(symbol="LRRK2", disease_relevance_score=0.8)],
        limit_per_target=2,
    )

    activity_calls = [
        call for call in session.calls if call["url"].endswith("/activity.json")
    ]
    assert [call["params"]["offset"] for call in activity_calls] == [0, 2]
    assert {record["identifiers"]["chembl"] for record in records} == {
        "CHEMBL_A",
        "CHEMBL_B",
    }
    activity_evidence = [
        item
        for record in records
        for item in record["evidence"]
        if item["evidence_type"] == "activity"
    ]
    assert {item["source_record_id"] for item in activity_evidence} == {"ACT1", "ACT2"}
    act1 = next(item for item in activity_evidence if item["source_record_id"] == "ACT1")
    assert act1["source"] == "ChEMBL"
    assert act1["metadata"]["activity_id"] == "ACT1"
    assert act1["metadata"]["assay_chembl_id"] == "ASSAY1"
    assert act1["metadata"]["target_chembl_id"] == "CHEMBL_T1"
    assert act1["metadata"]["molecule_chembl_id"] == "CHEMBL_A"
    assert act1["metadata"]["standard_type"] == "IC50"
    assert act1["metadata"]["standard_value"] == 12.5
    assert act1["metadata"]["standard_units"] == "nM"
    assert act1["metadata"]["relation"] == "="
    assert act1["metadata"]["pchembl_value"] == 8.1
    assert act1["metadata"]["assay_type"] == "B"
    assert act1["metadata"]["response_provenance"]["mode"] == "live"
    assert act1["confidence"] >= 0.8


def test_chembl_activity_records_skip_missing_molecule_and_uninterpretable_rows():
    session = QueueSession(
        [
            MockResponse({"mechanisms": []}),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "NO_MOL",
                            "assay_chembl_id": "ASSAY_SKIP",
                            "standard_type": "IC50",
                            "standard_value": "10",
                        },
                        {
                            "activity_id": "NO_VALUE",
                            "molecule_chembl_id": "CHEMBL_SKIP",
                            "assay_chembl_id": "ASSAY_SKIP",
                            "standard_type": "IC50",
                        },
                        {
                            "activity_id": "IRRELEVANT",
                            "molecule_chembl_id": "CHEMBL_SKIP2",
                            "assay_chembl_id": "ASSAY_SKIP2",
                            "standard_type": "AUC",
                            "standard_value": "3",
                        },
                        {
                            "activity_id": "VALID",
                            "molecule_chembl_id": "CHEMBL_VALID",
                            "assay_chembl_id": "ASSAY_VALID",
                            "standard_type": "inhibition",
                            "standard_value": "68",
                            "standard_units": "%",
                        },
                    ],
                    "page_meta": {"next": None},
                }
            ),
            MockResponse({"molecules": [{"pref_name": "Valid activity"}]}),
            MockResponse(
                {
                    "assays": [
                        {
                            "assay_chembl_id": "ASSAY_VALID",
                            "description": "Inhibition assay",
                            "assay_type": "F",
                            "confidence_score": 8,
                        }
                    ]
                }
            ),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        target_mapper=StaticTargetMapper({"LRRK2": _chembl_mapping("LRRK2", "CHEMBL_T1")}),  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    records = adapter.retrieve_molecules(
        Disease(input_name="Disease", canonical_name="Disease"),
        [Target(symbol="LRRK2", disease_relevance_score=0.8)],
    )

    assert [record["identifiers"]["chembl"] for record in records] == ["CHEMBL_VALID"]
    activity_evidence = [
        item
        for item in records[0]["evidence"]
        if item["evidence_type"] == "activity"
    ]
    assert [item["source_record_id"] for item in activity_evidence] == ["VALID"]
    assert activity_evidence[0]["metadata"]["standard_type"] == "INHIBITION"


def test_chembl_activity_records_deduplicate_exact_activity_ids_and_aggregate_targets():
    session = QueueSession(
        [
            MockResponse({"mechanisms": []}),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "DUP",
                            "molecule_chembl_id": "CHEMBL_SHARED",
                            "assay_chembl_id": "ASSAY1",
                            "standard_type": "IC50",
                            "standard_value": "10",
                        },
                        {
                            "activity_id": "DUP",
                            "molecule_chembl_id": "CHEMBL_SHARED",
                            "assay_chembl_id": "ASSAY1",
                            "standard_type": "IC50",
                            "standard_value": "10",
                        },
                    ],
                    "page_meta": {"next": None},
                }
            ),
            MockResponse({"molecules": [{"pref_name": "Shared molecule"}]}),
            MockResponse(
                {
                    "assays": [
                        {
                            "assay_chembl_id": "ASSAY1",
                            "description": "Binding assay",
                            "assay_type": "B",
                            "confidence_score": 9,
                        }
                    ]
                }
            ),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
            MockResponse({"mechanisms": []}),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "UNIQUE",
                            "molecule_chembl_id": "CHEMBL_SHARED",
                            "assay_chembl_id": "ASSAY2",
                            "standard_type": "Kd",
                            "standard_value": "18",
                        }
                    ],
                    "page_meta": {"next": None},
                }
            ),
            MockResponse({"molecules": [{"pref_name": "Shared molecule"}]}),
            MockResponse(
                {
                    "assays": [
                        {
                            "assay_chembl_id": "ASSAY2",
                            "description": "Affinity assay",
                            "assay_type": "B",
                            "confidence_score": 8,
                        }
                    ]
                }
            ),
            MockResponse({"drug_indications": []}),
            MockResponse({"drug_warnings": []}),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        target_mapper=StaticTargetMapper(
            {
                "LRRK2": _chembl_mapping("LRRK2", "CHEMBL_T1"),
                "SNCA": _chembl_mapping("SNCA", "CHEMBL_T2"),
            }
        ),  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    records = adapter.retrieve_molecules(
        Disease(input_name="Disease", canonical_name="Disease"),
        [
            Target(symbol="LRRK2", disease_relevance_score=0.8),
            Target(symbol="SNCA", disease_relevance_score=0.7),
        ],
        limit_per_target=5,
    )

    assert len(records) == 1
    assert records[0]["known_targets"] == ["LRRK2", "SNCA"]
    activity_ids = [
        item["source_record_id"]
        for item in records[0]["evidence"]
        if item["evidence_type"] == "activity"
    ]
    assert activity_ids == ["DUP", "UNIQUE"]


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


def test_chembl_retries_429_and_records_retry_metadata():
    session = QueueSession(
        [
            MockResponse({}, status_code=429),
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
    assert adapter.last_trace_metadata["retry_count"] == 1
    assert adapter.last_trace_metadata["rate_limit_retry_count"] == 1
    assert adapter._last_response_provenance["retry_count"] == 1
    assert adapter._last_response_provenance["status_code"] == 200


def test_chembl_does_not_retry_non_rate_limited_400_errors():
    session = QueueSession([MockResponse({}, status_code=400)])
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        max_retries=3,
        retry_delay_seconds=0,
    )

    with pytest.raises(ExternalDataUnavailableError):
        adapter._get("target.json", {"limit": 1})

    assert len(session.calls) == 1


def test_chembl_respects_activity_indication_and_warning_limits_with_trace_metadata():
    session = QueueSession(
        [
            MockResponse({"mechanisms": []}),
            MockResponse(
                {
                    "activities": [
                        {
                            "activity_id": "ACT1",
                            "molecule_chembl_id": "CHEMBL_A",
                            "assay_chembl_id": "ASSAY1",
                            "standard_type": "IC50",
                            "standard_value": "10",
                        },
                        {
                            "activity_id": "ACT2",
                            "molecule_chembl_id": "CHEMBL_B",
                            "assay_chembl_id": "ASSAY2",
                            "standard_type": "IC50",
                            "standard_value": "20",
                        },
                    ],
                    "page_meta": {"next": "activity.json?offset=2"},
                }
            ),
            MockResponse({"molecules": [{"pref_name": "Activity A"}]}),
            MockResponse({"assays": [{"assay_chembl_id": "ASSAY1", "confidence_score": 8}]}),
            MockResponse(
                {
                    "drug_indications": [
                        {"drugind_id": "IND1", "mesh_heading": "Disease A"},
                        {"drugind_id": "IND2", "mesh_heading": "Disease B"},
                    ],
                    "page_meta": {"next": "drug_indication.json?offset=2"},
                }
            ),
            MockResponse(
                {
                    "drug_warnings": [
                        {"warning_id": "WARN1", "warning_type": "Warning A"},
                        {"warning_id": "WARN2", "warning_type": "Warning B"},
                    ],
                    "page_meta": {"next": "drug_warning.json?offset=2"},
                }
            ),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        target_mapper=StaticTargetMapper({"LRRK2": _chembl_mapping("LRRK2", "CHEMBL_T1")}),  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
        max_activity_records_per_target=1,
        max_indications_per_molecule=1,
        max_warnings_per_molecule=1,
    )

    records = adapter.retrieve_molecules(
        Disease(input_name="Disease", canonical_name="Disease"),
        [Target(symbol="LRRK2", disease_relevance_score=0.8)],
        limit_per_target=5,
    )

    evidence_types = [item["evidence_type"] for item in records[0]["evidence"]]
    assert evidence_types.count("activity") == 1
    assert evidence_types.count("indication") == 1
    assert evidence_types.count("safety_warning") == 1
    assert records[0]["warnings"]
    assert any("truncated" in warning.lower() for warning in records[0]["warnings"])
    assert adapter.last_trace_metadata["pages_fetched"] >= 3
    assert adapter.last_trace_metadata["records_fetched"] >= 6
    assert adapter.last_trace_metadata["records_retained"] >= 3
    assert adapter.last_trace_metadata["truncated"] is True


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
    assert "Optional ChEMBL molecule-detail enrichment unavailable" in records[0]["warnings"][0]


def test_chembl_no_records_raises_no_candidates_after_successful_target_mapping():
    session = QueueSession(
        [
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL_NONE",
                            "organism": "Homo sapiens",
                            "target_type": "SINGLE PROTEIN",
                            "pref_name": "No molecule target",
                        }
                    ]
                }
            ),
            MockResponse({"mechanisms": []}),
            MockResponse({"activities": []}),
        ]
    )
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
        name="No molecule target",
        disease_relevance_score=0.1,
        evidence=[],
        mechanism=None,
    )

    with pytest.raises(NoCandidatesFoundError):
        adapter.retrieve_molecules(disease, [target])


class StaticTargetMapper:
    def __init__(self, mappings: dict[str, ChEMBLTargetMapping | None]) -> None:
        self.mappings = mappings
        self.warnings: list[str] = []

    def map_target(self, target: Target) -> ChEMBLTargetMapping | None:
        mapping = self.mappings.get(target.symbol)
        if mapping is None:
            self.warnings.append(f"No ChEMBL target mapping for {target.symbol}.")
        return mapping


def _chembl_mapping(symbol: str, chembl_id: str) -> ChEMBLTargetMapping:
    return ChEMBLTargetMapping(
        input_target_symbol=symbol,
        input_identifiers={},
        chembl_target_id=chembl_id,
        target_type="SINGLE PROTEIN",
        organism="Homo sapiens",
        pref_name=f"{symbol} protein",
        confidence=0.9,
        mapping_method="test",
        source_record_id=chembl_id,
        metadata={},
    )


def test_chembl_partial_target_mapping_continues_with_warning():
    session = QueueSession(
        [
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
                        {"pref_name": "Mapped molecule", "molecule_type": "Small molecule"}
                    ]
                }
            ),
        ]
    )
    mapper = StaticTargetMapper({"MAPPED": _chembl_mapping("MAPPED", "CHEMBL_T1")})
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        target_mapper=mapper,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )
    disease = Disease(input_name="Disease", canonical_name="Disease")
    targets = [
        Target(symbol="UNMAPPED", disease_relevance_score=0.8),
        Target(symbol="MAPPED", disease_relevance_score=0.7),
    ]

    records = adapter.retrieve_molecules(disease, targets, limit_per_target=1)

    assert records[0]["identifiers"]["chembl"] == "CHEMBL1"
    assert any("UNMAPPED" in warning for warning in records[0]["warnings"])
    assert session.calls[0]["params"]["target_chembl_id"] == "CHEMBL_T1"


def test_chembl_no_target_mappings_for_all_targets_fails():
    adapter = ChEMBLAdapter(
        session=QueueSession([]),  # type: ignore[arg-type]
        target_mapper=StaticTargetMapper({}),  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )
    disease = Disease(input_name="Disease", canonical_name="Disease")

    with pytest.raises(MoleculeRetrievalError, match="No ChEMBL target mappings"):
        adapter.retrieve_molecules(disease, [Target(symbol="NONE", disease_relevance_score=0.1)])


def test_http_cache_expires_payloads_and_keeps_provenance(tmp_path):
    cache = HttpResponseCache(tmp_path)
    key = cache.build_key(
        source_name="ChEMBL",
        endpoint="https://www.ebi.ac.uk/chembl/api/data/target.json",
        method="GET",
        query_params={"limit": 1},
    )

    cache.write_success(
        cache_key=key,
        response_json={"targets": []},
        source="ChEMBL",
        endpoint="https://www.ebi.ac.uk/chembl/api/data/target.json",
        method="GET",
        request_metadata={"query_params": {"limit": 1}},
        ttl_seconds=60,
    )

    cached = cache.get(key, ttl_seconds=60)
    assert cached is not None
    assert cached.response_json == {"targets": []}
    assert cached.source == "ChEMBL"
    assert cached.request_metadata["method"] == "GET"
    assert cache.get(key, ttl_seconds=0) is None


def test_adapter_health_checks_report_typed_public_endpoint_status():
    open_targets_session = QueueSession([MockResponse({"data": {"__typename": "Query"}})])
    chembl_session = QueueSession([MockResponse({"status": "UP"})])
    open_targets = OpenTargetsAdapter(
        session=open_targets_session  # type: ignore[arg-type]
    )
    chembl = ChEMBLAdapter(
        session=chembl_session,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    open_targets_status = open_targets.health_check()
    chembl_status = chembl.health_check()

    assert isinstance(open_targets_status, AdapterHealthStatus)
    assert isinstance(chembl_status, AdapterHealthStatus)
    assert open_targets_status.source_name == "Open Targets"
    assert open_targets_status.ok is True
    assert open_targets_status.latency_ms is not None
    assert open_targets_status.error is None
    assert chembl_status.source_name == "ChEMBL"
    assert chembl_status.ok is True
    assert chembl_status.latency_ms is not None
    assert chembl_status.error is None
    assert open_targets_session.calls[0]["timeout"] <= 5.0
    assert chembl_session.calls[0]["timeout"] <= 5.0


def test_chembl_health_check_falls_back_when_status_endpoint_fails():
    session = QueueSession(
        [
            MockResponse({"error": "temporary"}, status_code=500),
            MockResponse({"molecules": [{"molecule_chembl_id": "CHEMBL25"}]}),
        ]
    )
    adapter = ChEMBLAdapter(
        session=session,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    status = adapter.health_check()

    assert status.ok is True
    assert status.endpoint.endswith("/molecule.json")
    assert status.error is None
    assert status.metadata["probe"] == "molecule"
    assert status.metadata["status_endpoint"].endswith("/status.json")
    assert len(session.calls) == 2
    assert session.calls[0]["url"].endswith("/status.json")
    assert session.calls[1]["url"].endswith("/molecule.json")


def test_pubchem_health_check_uses_minimal_non_disease_request():
    session = QueueSession([MockResponse({"IdentifierList": {"CID": [2244]}})])
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    status = adapter.health_check()

    assert status.source_name == "PubChem"
    assert status.ok is True
    assert status.endpoint.endswith("/compound/name/aspirin/cids/JSON")
    assert status.latency_ms is not None
    assert status.error is None
    assert session.calls[0]["timeout"] <= 5.0


def test_adapter_health_check_reports_failures_without_raising():
    adapter = OpenTargetsAdapter(session=QueueSession(error=requests.Timeout("too slow")))  # type: ignore[arg-type]

    status = adapter.health_check()

    assert status.source_name == "Open Targets"
    assert status.ok is False
    assert status.endpoint == OpenTargetsAdapter.default_endpoint
    assert status.latency_ms is not None
    assert "too slow" in str(status.error)


def test_pubchem_prefers_inchikey_lookup_and_stores_structure_metadata():
    session = QueueSession(
        [
            MockResponse({"IdentifierList": {"CID": [2244]}}),
            MockResponse(
                {
                    "PropertyTable": {
                        "Properties": [
                            {
                                "CID": 2244,
                                "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                                "IsomericSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                                "InChI": (
                                    "InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-"
                                    "7(8)9(11)12/h2-5H,1H3,(H,11,12)"
                                ),
                                "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                                "MolecularFormula": "C9H8O4",
                                "MolecularWeight": 180.16,
                            }
                        ]
                    }
                }
            ),
            MockResponse(
                {
                    "InformationList": {
                        "Information": [{"Synonym": [f"synonym-{index}" for index in range(25)]}]
                    }
                }
            ),
        ]
    )
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    enriched = adapter.annotate_molecule(
        {
            "name": "aspirin",
            "identifiers": {"inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"},
            "evidence": [
                {
                    "source": "ChEMBL",
                    "source_record_id": "mec-1",
                    "title": "Mechanism",
                    "evidence_type": "mechanism",
                    "summary": "ChEMBL evidence.",
                    "confidence": 0.8,
                    "metadata": {},
                }
            ],
        }
    )

    assert "/compound/inchikey/BSYNRYMUTXBXSQ-UHFFFAOYSA-N/cids/JSON" in session.calls[0]["url"]
    assert enriched["identifiers"]["pubchem_cid"] == "2244"
    assert enriched["identifiers"]["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert enriched["identifiers"]["inchi"].startswith("InChI=1S/C9H8O4")
    assert enriched["chemical_metadata"]["canonical_smiles"] == "CC(=O)OC1=CC=CC=C1C(=O)O"
    assert enriched["chemical_metadata"]["isomeric_smiles"] == "CC(=O)OC1=CC=CC=C1C(=O)O"
    assert enriched["chemical_metadata"]["molecular_formula"] == "C9H8O4"
    assert enriched["chemical_metadata"]["molecular_weight"] == 180.16
    assert len(enriched["chemical_metadata"]["synonyms"]) == 20
    assert enriched["evidence"][-1]["source"] == "PubChem"
    assert enriched["evidence"][-1]["source_record_id"] == "2244"
    assert enriched["evidence"][-1]["evidence_type"] == "chemical_annotation"
    assert enriched["evidence"][-1]["metadata"]["lookup"]["method"] == "inchikey"


def test_pubchem_name_lookup_is_fallback_only_when_better_identifiers_are_unavailable():
    session = QueueSession(
        [
            MockResponse({"IdentifierList": {"CID": [2244]}}),
            MockResponse({"PropertyTable": {"Properties": [{"CID": 2244}]}}),
            MockResponse({"InformationList": {"Information": [{"Synonym": ["aspirin"]}]}}),
        ]
    )
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    enriched = adapter.annotate_molecule({"name": "aspirin", "identifiers": {"chembl": "CHEMBL25"}})

    assert "/compound/name/aspirin/cids/JSON" in session.calls[0]["url"]
    assert enriched["evidence"][-1]["metadata"]["lookup"]["method"] == "name"
    assert enriched["identifiers"]["pubchem_cid"] == "2244"


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
    assert not any(item.get("source") == "PubChem" for item in annotated[0]["evidence"])
    assert "PubChem returned no record" in annotated[0]["warnings"][0]


def test_pubchem_single_molecule_missing_record_raises_evidence_error():
    session = QueueSession([MockResponse({}, status_code=404)])
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    with pytest.raises(EvidenceRetrievalError):
        adapter.annotate_molecule({"name": "GANTENERUMAB", "identifiers": {}})


def test_pubchem_rejects_ambiguous_name_lookup_without_fake_evidence():
    session = QueueSession([MockResponse({"IdentifierList": {"CID": [1, 2]}})])
    adapter = PubChemAdapter(session=session)  # type: ignore[arg-type]

    with pytest.raises(EvidenceRetrievalError, match="ambiguous"):
        adapter.annotate_molecule({"name": "ambiguous", "identifiers": {}})
