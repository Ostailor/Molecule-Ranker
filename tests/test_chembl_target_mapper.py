from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.data_sources.chembl_target_mapper import ChEMBLTargetMapper
from molecule_ranker.data_sources.errors import MoleculeRetrievalError
from molecule_ranker.schemas import Target
from tests.test_data_sources import MockResponse, QueueSession


def _target(**overrides: Any) -> Target:
    payload: dict[str, Any] = {
        "symbol": "LRRK2",
        "name": "leucine-rich repeat kinase 2",
        "identifiers": {"uniprot": "Q5S007", "ensembl": "ENSG00000188906"},
        "disease_relevance_score": 0.8,
    }
    payload.update(overrides)
    return Target(**payload)


def test_chembl_target_mapper_maps_by_uniprot_accession():
    session = QueueSession(
        [
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL6152",
                            "target_type": "SINGLE PROTEIN",
                            "organism": "Homo sapiens",
                            "pref_name": "Leucine-rich repeat serine/threonine-protein kinase 2",
                        }
                    ]
                }
            )
        ]
    )
    mapper = ChEMBLTargetMapper(session=session)  # type: ignore[arg-type]

    mapping = mapper.map_target(_target())

    assert mapping is not None
    assert mapping.chembl_target_id == "CHEMBL6152"
    assert mapping.input_target_symbol == "LRRK2"
    assert mapping.input_identifiers["uniprot"] == "Q5S007"
    assert mapping.mapping_method == "uniprot_accession"
    assert mapping.confidence == pytest.approx(0.95)
    assert mapping.source_record_id == "CHEMBL6152"
    assert session.calls[0]["params"]["target_components__accession"] == "Q5S007"


def test_chembl_target_mapper_maps_by_symbol_when_uniprot_missing():
    session = QueueSession(
        [
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL2095189",
                            "target_type": "SINGLE PROTEIN",
                            "organism": "Homo sapiens",
                            "pref_name": "Alpha-synuclein",
                        }
                    ]
                }
            )
        ]
    )
    mapper = ChEMBLTargetMapper(session=session)  # type: ignore[arg-type]

    mapping = mapper.map_target(_target(symbol="SNCA", identifiers={}))

    assert mapping is not None
    assert mapping.chembl_target_id == "CHEMBL2095189"
    assert mapping.mapping_method == "approved_symbol_exact_synonym"
    assert mapping.confidence == pytest.approx(0.8)


def test_chembl_target_mapper_continues_after_identifier_lookup_failure():
    session = QueueSession(
        [
            MockResponse({"error": "temporary"}, status_code=500),
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL2095189",
                            "target_type": "SINGLE PROTEIN",
                            "organism": "Homo sapiens",
                            "pref_name": "Alpha-synuclein",
                        }
                    ]
                }
            ),
        ]
    )
    mapper = ChEMBLTargetMapper(
        session=session,  # type: ignore[arg-type]
        max_retries=0,
        retry_delay_seconds=0,
    )

    mapping = mapper.map_target(
        _target(symbol="SNCA", identifiers={"ensembl": "ENSG00000145335"})
    )

    assert mapping is not None
    assert mapping.chembl_target_id == "CHEMBL2095189"
    assert mapping.mapping_method == "approved_symbol_exact_synonym"
    assert any("ensembl_xref" in warning for warning in mapper.warnings)


def test_chembl_target_mapper_rejects_ambiguous_mapping():
    session = QueueSession(
        [
            MockResponse(
                {
                    "targets": [
                        {
                            "target_chembl_id": "CHEMBL1",
                            "target_type": "SINGLE PROTEIN",
                            "organism": "Homo sapiens",
                            "pref_name": "Ambiguous 1",
                        },
                        {
                            "target_chembl_id": "CHEMBL2",
                            "target_type": "SINGLE PROTEIN",
                            "organism": "Homo sapiens",
                            "pref_name": "Ambiguous 2",
                        },
                    ]
                }
            )
        ]
    )
    mapper = ChEMBLTargetMapper(session=session)  # type: ignore[arg-type]

    mapping = mapper.map_target(_target())

    assert mapping is None
    assert mapper.warnings
    assert "ambiguous" in mapper.warnings[0].lower()


def test_chembl_target_mapper_all_unmapped_fails():
    session = QueueSession([MockResponse({"targets": []}), MockResponse({"targets": []})])
    mapper = ChEMBLTargetMapper(session=session)  # type: ignore[arg-type]

    with pytest.raises(MoleculeRetrievalError):
        mapper.map_targets_or_raise([_target(symbol="NONE", identifiers={})])
