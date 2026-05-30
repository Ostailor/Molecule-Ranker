from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.structure.binding_site import BindingSiteConfig, define_binding_site
from molecule_ranker.structure.schemas import StructureRecord


def _record(**overrides: Any) -> StructureRecord:
    payload: dict[str, Any] = {
        "structure_id": "RCSB_PDB:6LIG",
        "source": "RCSB_PDB",
        "external_id": "6LIG",
        "target_symbol": "LRRK2",
        "target_identifiers": {"uniprot": "Q5S007"},
        "structure_type": "experimental",
        "experimental_method": "X-ray diffraction",
        "resolution_angstrom": 2.0,
        "coverage": {"overall": 0.9},
        "chains": ["A"],
        "ligands": [],
        "mutations": [],
        "organism": "Homo sapiens",
        "release_date": "2020-01-02",
        "quality_metrics": {"target_mapping_confidence": 0.95},
        "url": "https://www.rcsb.org/structure/6LIG",
        "retrieved_at": datetime(2026, 1, 2, tzinfo=UTC),
        "metadata": {"binding_site_evidence": False},
    }
    payload.update(overrides)
    return StructureRecord(**payload)


def test_co_crystal_site_extracted_from_mocked_structure_metadata() -> None:
    structure = _record(
        ligands=[
            {
                "ligand_id": "ATP",
                "relationship": "relevant",
                "coordinates": [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]],
            }
        ],
        metadata={"binding_site_evidence": True},
    )

    site = define_binding_site(structure, config=BindingSiteConfig())

    assert site.method == "co_crystal_ligand"
    assert site.reference_ligand_id == "ATP"
    assert site.center == [1.0, 1.0, 1.0]
    assert site.box_size == [12.0, 12.0, 12.0]
    assert site.metadata["provenance"] == "structure_ligand_coordinates"
    assert site.confidence > 0.7


def test_user_supplied_box_validated() -> None:
    structure = _record(structure_id="RCSB_PDB:6APO", external_id="6APO")

    site = define_binding_site(
        structure,
        config=BindingSiteConfig(
            method="user_supplied_box",
            user_center=[1.0, 2.0, 3.0],
            user_box_size=[18.0, 16.0, 14.0],
            user_box_source="operator_artifact:site-1",
        ),
    )

    assert site.method == "user_supplied_box"
    assert site.center == [1.0, 2.0, 3.0]
    assert site.box_size == [18.0, 16.0, 14.0]
    assert site.metadata["provenance"] == "operator_artifact:site-1"

    with pytest.raises(ValueError, match="box size"):
        define_binding_site(
            structure,
            config=BindingSiteConfig(
                method="user_supplied_box",
                user_center=[1.0, 2.0, 3.0],
                user_box_size=[18.0, -1.0, 14.0],
                user_box_source="operator_artifact:site-1",
            ),
        )


def test_invented_residue_source_rejected() -> None:
    structure = _record()

    with pytest.raises(ValueError, match="must have curated or user provenance"):
        define_binding_site(
            structure,
            config=BindingSiteConfig(
                method="known_residues",
                known_residues=["LYS1906", "ASP2017"],
                known_residue_source="codex_generated",
            ),
        )


def test_blind_docking_disabled_by_default() -> None:
    structure = _record(structure_id="AlphaFold_DB:AF-Q5S007-F1", structure_type="predicted")

    site = define_binding_site(
        structure,
        config=BindingSiteConfig(method="full_protein_blind"),
    )

    assert site.method == "unavailable"
    assert site.confidence == 0.0
    assert site.metadata["docking_skipped"] is True
    assert any("blind docking" in warning.lower() for warning in site.warnings)
