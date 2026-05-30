from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.schemas import Target
from molecule_ranker.structure.adapters import (
    AlphaFoldStructureAdapter,
    RCSBStructureAdapter,
    UserStructureAdapter,
)
from molecule_ranker.structure.sources import check_structure_source_health


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


def _target(**overrides: Any) -> Target:
    payload: dict[str, Any] = {
        "symbol": "LRRK2",
        "name": "Leucine-rich repeat kinase 2",
        "identifiers": {
            "uniprot": "Q5S007",
            "chembl_target_id": "CHEMBL1075104",
            "opentargets_id": "ENSG00000188906",
        },
        "disease_relevance_score": 0.8,
    }
    payload.update(overrides)
    return Target(**payload)


def test_mocked_rcsb_retrieval_preserves_metadata_and_raw_artifact(tmp_path: Path) -> None:
    session = QueueSession(
        [
            MockResponse({"result_set": [{"identifier": "6XYZ"}]}),
            MockResponse(
                {
                    "exptl": [{"method": "X-RAY DIFFRACTION"}],
                    "rcsb_accession_info": {"initial_release_date": "2020-01-02"},
                    "rcsb_entry_info": {
                        "resolution_combined": [1.8],
                        "experimental_method": ["X-ray diffraction"],
                    },
                    "rcsb_entry_container_identifiers": {
                        "polymer_entity_ids": ["1"],
                        "entry_id": "6XYZ",
                    },
                    "nonpolymer_entities": [
                        {
                            "pdbx_entity_nonpoly": {"comp_id": "ATP"},
                            "chem_comp": {"id": "ATP", "name": "ATP"},
                        }
                    ],
                }
            ),
            MockResponse(
                {
                    "entity_poly": {"rcsb_sample_sequence_length": 2527},
                    "rcsb_polymer_entity": {"pdbx_description": "LRRK2 kinase domain"},
                    "rcsb_polymer_entity_container_identifiers": {
                        "auth_asym_ids": ["A"],
                        "reference_sequence_identifiers": [
                            {
                                "database_name": "UniProt",
                                "database_accession": "Q5S007",
                            }
                        ],
                    },
                    "rcsb_entity_source_organism": [{"scientific_name": "Homo sapiens"}],
                }
            ),
        ]
    )
    adapter = RCSBStructureAdapter(
        session=session,
        cache_dir=tmp_path / "cache",
        raw_artifact_dir=tmp_path / "raw",
    )

    records = adapter.retrieve(_target(), limit=5)

    assert len(records) == 1
    record = records[0]
    assert record.structure_id == "RCSB_PDB:6XYZ"
    assert record.source == "RCSB_PDB"
    assert record.external_id == "6XYZ"
    assert record.target_symbol == "LRRK2"
    assert record.target_identifiers["uniprot"] == "Q5S007"
    assert record.structure_type == "experimental"
    assert record.experimental_method == "X-ray diffraction"
    assert record.resolution_angstrom == 1.8
    assert record.chains == ["A"]
    assert record.ligands[0]["ligand_id"] == "ATP"
    assert record.organism == "Homo sapiens"
    assert record.release_date == "2020-01-02"
    assert record.url == "https://www.rcsb.org/structure/6XYZ"
    assert record.metadata["biological_relevance_not_assumed"] is True
    raw_path = Path(record.metadata["raw_metadata_artifact"])
    assert raw_path.exists()
    assert json.loads(raw_path.read_text())["external_id"] == "6XYZ"
    assert session.calls[0]["json"]["query"]["type"] == "group"
    assert "Q5S007" in json.dumps(session.calls[0]["json"])
    assert "CHEMBL1075104" in json.dumps(session.calls[0]["json"])
    assert adapter.warnings == []


def test_mocked_rcsb_ambiguous_target_mapping_is_rejected(tmp_path: Path) -> None:
    session = QueueSession(
        [
            MockResponse({"result_set": [{"identifier": "6BAD"}]}),
            MockResponse(
                {
                    "exptl": [{"method": "X-RAY DIFFRACTION"}],
                    "rcsb_entry_container_identifiers": {"polymer_entity_ids": ["1"]},
                }
            ),
            MockResponse(
                {
                    "rcsb_polymer_entity_container_identifiers": {
                        "auth_asym_ids": ["A"],
                        "reference_sequence_identifiers": [
                            {
                                "database_name": "UniProt",
                                "database_accession": "P99999",
                            }
                        ],
                    }
                }
            ),
        ]
    )
    adapter = RCSBStructureAdapter(
        session=session,
        cache_dir=tmp_path / "cache",
        raw_artifact_dir=tmp_path / "raw",
    )

    records = adapter.retrieve(_target(), limit=5)

    assert records == []
    assert any("ambiguous" in warning.lower() for warning in adapter.warnings)


def test_mocked_alphafold_retrieval_marks_predicted_lower_confidence(
    tmp_path: Path,
) -> None:
    session = QueueSession(
        [
            MockResponse(
                [
                    {
                        "entryId": "AF-Q5S007-F1",
                        "uniprotAccession": "Q5S007",
                        "globalMetricValue": 92.4,
                        "cifUrl": "https://example.test/model.cif",
                        "pdbUrl": "https://example.test/model.pdb",
                        "paeDocUrl": "https://example.test/pae.json",
                        "organismScientificName": "Homo sapiens",
                    }
                ]
            )
        ]
    )
    adapter = AlphaFoldStructureAdapter(
        session=session,
        cache_dir=tmp_path / "cache",
        raw_artifact_dir=tmp_path / "raw",
    )

    records = adapter.retrieve(_target(), limit=5)

    assert len(records) == 1
    record = records[0]
    assert record.source == "AlphaFold_DB"
    assert record.structure_type == "predicted"
    assert record.experimental_method == "computed model"
    assert record.quality_metrics["confidence_metadata"]["global_metric_value"] == 92.4
    assert record.quality_metrics["relative_confidence"] == "lower_than_suitable_experimental"
    assert record.metadata["not_equivalent_to_experimental_co_crystal"] is True
    assert record.metadata["confidence_cap"] <= 0.55
    assert Path(record.metadata["raw_metadata_artifact"]).exists()


def test_user_structure_adapter_blocks_path_traversal_and_hashes_allowed_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    structure_file = root / "model.pdb"
    structure_file.write_text("HEADER TEST STRUCTURE\n")
    outside = tmp_path / "outside.pdb"
    outside.write_text("HEADER OUTSIDE\n")
    adapter = UserStructureAdapter(allowed_roots=[root])

    record = adapter.load(
        structure_file,
        target_symbol="LRRK2",
        target_identifiers={"uniprot": "Q5S007"},
        metadata={"provided_by": "scientist@example.com"},
    )

    assert record.source == "user_supplied"
    assert record.structure_type == "user_supplied"
    assert record.external_id == str(structure_file.resolve())
    assert record.metadata["sha256"]
    assert record.metadata["user_provenance"]["provided_by"] == "scientist@example.com"
    assert "not trusted without metadata review" in " ".join(record.metadata["warnings"])

    with pytest.raises(PermissionError, match="allowed artifact roots"):
        adapter.load(outside, target_symbol="LRRK2")


def test_structure_source_health_checks_report_adapter_status(tmp_path: Path) -> None:
    session = QueueSession([MockResponse({"result_set": []})])
    adapter = RCSBStructureAdapter(
        session=session,
        cache_dir=tmp_path / "cache",
        raw_artifact_dir=tmp_path / "raw",
    )

    statuses = check_structure_source_health([adapter])

    assert len(statuses) == 1
    assert statuses[0].source == "RCSB_PDB"
    assert statuses[0].ok is True
    assert statuses[0].checked_at.tzinfo is not None
