from __future__ import annotations

from typing import Any

from molecule_ranker.data_sources.structure_adapters import AlphaFoldDBAdapter, RCSBPDBAdapter
from molecule_ranker.developability.structure import TargetStructureRecord, select_target_structure
from molecule_ranker.schemas import Target


class MockResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class QueueSession:
    def __init__(self, responses: list[MockResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


def _target() -> Target:
    return Target(
        symbol="LRRK2",
        name="Leucine-rich repeat kinase 2",
        identifiers={"uniprot": "Q5S007"},
        disease_relevance_score=0.8,
    )


def test_mocked_rcsb_structure_retrieval():
    session = QueueSession(
        [
            MockResponse({"result_set": [{"identifier": "6XYZ"}]}),
            MockResponse(
                {
                    "exptl": [{"method": "X-RAY DIFFRACTION"}],
                    "rcsb_entry_info": {"resolution_combined": [1.8]},
                    "rcsb_entry_container_identifiers": {"polymer_entity_ids": ["1"]},
                    "nonpolymer_entities": [
                        {
                            "pdbx_entity_nonpoly": {"comp_id": "ATP"},
                            "chem_comp": {"id": "ATP"},
                        }
                    ],
                }
            ),
            MockResponse(
                {
                    "rcsb_polymer_entity_container_identifiers": {
                        "auth_asym_ids": ["A", "B"],
                        "reference_sequence_identifiers": [
                            {
                                "database_name": "UniProt",
                                "database_accession": "Q5S007",
                            }
                        ],
                    }
                }
            ),
        ]
    )

    records = RCSBPDBAdapter(session=session).retrieve_target_structures(_target())

    assert len(records) == 1
    record = records[0]
    assert record.structure_id == "6XYZ"
    assert record.source == "RCSB PDB"
    assert record.structure_kind == "experimental"
    assert record.method == "X-RAY DIFFRACTION"
    assert record.resolution == 1.8
    assert record.chains == ["A", "B"]
    assert record.ligands == ["ATP"]
    assert record.uniprot_accessions == ["Q5S007"]
    assert record.provenance["source"] == "RCSB PDB"
    assert session.calls[0]["json"]["query"]["parameters"]["value"] == "Q5S007"


def test_mocked_alphafold_retrieval():
    session = QueueSession(
        [
            MockResponse(
                [
                    {
                        "entryId": "AF-Q5S007-F1",
                        "uniprotAccession": "Q5S007",
                        "globalMetricValue": 87.5,
                        "cifUrl": "https://example.test/model.cif",
                        "pdbUrl": "https://example.test/model.pdb",
                        "paeDocUrl": "https://example.test/pae.json",
                    }
                ]
            )
        ]
    )

    records = AlphaFoldDBAdapter(session=session).retrieve_target_structures(_target())

    assert len(records) == 1
    record = records[0]
    assert record.structure_id == "AF-Q5S007-F1"
    assert record.source == "AlphaFold DB"
    assert record.structure_kind == "predicted"
    assert record.uniprot_accessions == ["Q5S007"]
    assert record.confidence < 0.6
    assert record.metadata["confidence_metadata"]["global_metric_value"] == 87.5
    assert session.calls[0]["url"].endswith("/prediction/Q5S007")


def test_experimental_structure_preferred_over_predicted_structure():
    predicted = TargetStructureRecord(
        target_symbol="LRRK2",
        structure_id="AF-Q5S007-F1",
        source="AlphaFold DB",
        structure_kind="predicted",
        uniprot_accessions=["Q5S007"],
        confidence=0.55,
    )
    experimental = TargetStructureRecord(
        target_symbol="LRRK2",
        structure_id="6XYZ",
        source="RCSB PDB",
        structure_kind="experimental",
        method="X-RAY DIFFRACTION",
        resolution=2.4,
        chains=["A"],
        ligands=["ATP"],
        uniprot_accessions=["Q5S007"],
        confidence=0.82,
    )

    selection = select_target_structure(
        [predicted, experimental],
        target_symbol="LRRK2",
        preferred_uniprot="Q5S007",
    )

    assert selection.selected_structure is not None
    assert selection.selected_structure.structure_id == "6XYZ"
    assert selection.warnings == []


def test_no_structure_returns_warning_not_fake_structure():
    selection = select_target_structure([], target_symbol="NONE", preferred_uniprot="P00000")

    assert selection.selected_structure is None
    assert selection.candidates == []
    assert selection.warnings
    assert "skipped" in selection.warnings[0].lower()
