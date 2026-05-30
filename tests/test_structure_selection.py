from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from molecule_ranker.structure.schemas import StructureRecord
from molecule_ranker.structure.selection import select_structure


def _record(**overrides: Any) -> StructureRecord:
    payload: dict[str, Any] = {
        "structure_id": "RCSB_PDB:6APO",
        "source": "RCSB_PDB",
        "external_id": "6APO",
        "target_symbol": "LRRK2",
        "target_identifiers": {"uniprot": "Q5S007"},
        "structure_type": "experimental",
        "experimental_method": "X-ray diffraction",
        "resolution_angstrom": 1.8,
        "coverage": {"overall": 0.9, "chain_A": 0.92, "binding_region": 0.88},
        "chains": ["A"],
        "ligands": [],
        "mutations": [],
        "organism": "Homo sapiens",
        "release_date": "2020-01-02",
        "quality_metrics": {"target_mapping_confidence": 0.95},
        "url": "https://www.rcsb.org/structure/6APO",
        "retrieved_at": datetime(2026, 1, 2, tzinfo=UTC),
        "metadata": {
            "binding_site_evidence": False,
            "chain_completeness": {"A": 0.92},
            "biological_relevance_not_assumed": True,
        },
    }
    payload.update(overrides)
    return StructureRecord(**payload)


def test_co_crystal_structure_with_relevant_ligand_is_preferred() -> None:
    apo = _record(structure_id="RCSB_PDB:6APO", external_id="6APO", ligands=[])
    co_crystal = _record(
        structure_id="RCSB_PDB:6LIG",
        external_id="6LIG",
        resolution_angstrom=2.2,
        ligands=[{"ligand_id": "ATP", "relationship": "relevant"}],
        metadata={
            "binding_site_evidence": True,
            "chain_completeness": {"A": 0.9},
            "biological_relevance_not_assumed": True,
        },
    )

    selection = select_structure(
        [apo, co_crystal],
        target_symbol="LRRK2",
        related_ligand_ids={"ATP"},
    )

    assert selection.selected_structure_id == "RCSB_PDB:6LIG"
    assert selection.selected_chain_ids == ["A"]
    assert selection.confidence > 0.75
    assert "co-crystal" in selection.selection_reason.lower()
    assert selection.rejected_structures[0]["structure_id"] == "RCSB_PDB:6APO"


def test_high_resolution_apo_preferred_over_weak_predicted_when_no_ligand() -> None:
    apo = _record(
        structure_id="RCSB_PDB:6APO",
        external_id="6APO",
        resolution_angstrom=1.6,
        ligands=[],
    )
    predicted = _record(
        structure_id="AlphaFold_DB:AF-Q5S007-F1",
        source="AlphaFold_DB",
        external_id="AF-Q5S007-F1",
        structure_type="predicted",
        experimental_method="computed model",
        resolution_angstrom=None,
        ligands=[],
        quality_metrics={
            "target_mapping_confidence": 0.95,
            "predicted_binding_region_confidence": 0.58,
        },
        metadata={"predicted_structure_lower_confidence": True},
    )

    selection = select_structure([predicted, apo], target_symbol="LRRK2")

    assert selection.selected_structure_id == "RCSB_PDB:6APO"
    assert "apo" in selection.selection_reason.lower()
    assert any(
        item["structure_id"] == "AlphaFold_DB:AF-Q5S007-F1"
        for item in selection.rejected_structures
    )


def test_predicted_structure_is_selected_with_lower_confidence_warning() -> None:
    predicted = _record(
        structure_id="AlphaFold_DB:AF-Q5S007-F1",
        source="AlphaFold_DB",
        external_id="AF-Q5S007-F1",
        structure_type="predicted",
        experimental_method="computed model",
        resolution_angstrom=None,
        ligands=[],
        coverage={"overall": 0.92, "binding_region": 0.88},
        quality_metrics={
            "target_mapping_confidence": 0.9,
            "predicted_binding_region_confidence": 0.86,
        },
        metadata={"predicted_structure_lower_confidence": True},
    )

    selection = select_structure([predicted], target_symbol="LRRK2")

    assert selection.selected_structure_id == "AlphaFold_DB:AF-Q5S007-F1"
    assert 0.0 < selection.confidence <= 0.65
    assert any("predicted" in warning.lower() for warning in selection.warnings)
    assert selection.metadata["applicability_domain"] == "lower_confidence_predicted_structure"


def test_poor_coverage_structure_is_rejected() -> None:
    poor = _record(
        structure_id="RCSB_PDB:LOWCOV",
        external_id="LOWCOV",
        coverage={"overall": 0.35, "chain_A": 0.35, "binding_region": 0.2},
    )

    selection = select_structure([poor], target_symbol="LRRK2")

    assert selection.selected_structure_id == "unavailable"
    assert selection.confidence == 0.0
    assert selection.rejected_structures[0]["structure_id"] == "RCSB_PDB:LOWCOV"
    assert "coverage" in " ".join(selection.rejected_structures[0]["reasons"]).lower()
    assert any("skipped" in warning.lower() for warning in selection.warnings)


def test_no_structure_returns_unavailable_selection() -> None:
    selection = select_structure([], target_symbol="LRRK2")

    assert selection.selected_structure_id == "unavailable"
    assert selection.selected_chain_ids == []
    assert selection.confidence == 0.0
    assert selection.metadata["applicability_domain"] == "unavailable"
    assert any("skipped" in warning.lower() for warning in selection.warnings)


def test_strict_structure_selection_raises_when_no_acceptable_structure() -> None:
    with pytest.raises(ValueError, match="No acceptable structure"):
        select_structure([], target_symbol="LRRK2", strict_structure_selection=True)
