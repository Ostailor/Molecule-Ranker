from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.structure.schemas import (
    BindingSiteDefinition,
    DockingPose,
    DockingRun,
    Ligand3DPreparation,
    ProteinLigandInteractionProfile,
    ReceptorPreparation,
    StructureAwareAssessment,
    StructureRecord,
    StructureSelection,
)


def _structure_record(**overrides: Any) -> StructureRecord:
    payload: dict[str, Any] = {
        "structure_id": "structure-1",
        "source": "RCSB_PDB",
        "external_id": "6XYZ",
        "target_symbol": "LRRK2",
        "target_identifiers": {"uniprot": "Q5S007"},
        "structure_type": "experimental",
        "experimental_method": "X-ray diffraction",
        "resolution_angstrom": 2.1,
        "coverage": {"chain_A": 0.92},
        "chains": ["A"],
        "ligands": [{"ligand_id": "ATP", "source": "RCSB_PDB"}],
        "mutations": [],
        "organism": "Homo sapiens",
        "release_date": "2020-01-02",
        "quality_metrics": {"rsr": 0.18},
        "url": "https://www.rcsb.org/structure/6XYZ",
        "retrieved_at": datetime(2026, 1, 2, tzinfo=UTC),
        "metadata": {"retrieval_policy": "test"},
    }
    payload.update(overrides)
    return StructureRecord(**payload)


def _selection(**overrides: Any) -> StructureSelection:
    payload: dict[str, Any] = {
        "selection_id": "selection-1",
        "target_symbol": "LRRK2",
        "selected_structure_id": "structure-1",
        "selected_chain_ids": ["A"],
        "selection_reason": "Suitable experimental structure with mapped UniProt accession.",
        "confidence": 0.8,
        "rejected_structures": [{"structure_id": "AF-Q5S007-F1", "reason": "predicted"}],
        "warnings": [],
        "metadata": {"policy": "conservative"},
    }
    payload.update(overrides)
    return StructureSelection(**payload)


def _receptor_prep(**overrides: Any) -> ReceptorPreparation:
    payload: dict[str, Any] = {
        "receptor_prep_id": "receptor-prep-1",
        "structure_id": "structure-1",
        "target_symbol": "LRRK2",
        "input_structure_path": "inputs/6XYZ.pdb",
        "prepared_receptor_path": "prepared/receptor.pdbqt",
        "preparation_method": "external_prepared_receptor",
        "protonation_policy": "externally reviewed",
        "kept_chains": ["A"],
        "removed_chains": ["B"],
        "kept_heterogens": ["ATP"],
        "removed_heterogens": ["HOH"],
        "missing_atoms_fixed": True,
        "missing_hydrogens_added": True,
        "missing_loops_modeled": False,
        "alternate_locations_resolved": True,
        "warnings": ["Prepared receptor is a computational artifact only."],
        "confidence": 0.7,
        "metadata": {},
    }
    payload.update(overrides)
    return ReceptorPreparation(**payload)


def _ligand_prep(**overrides: Any) -> Ligand3DPreparation:
    payload: dict[str, Any] = {
        "ligand_prep_id": "ligand-prep-1",
        "molecule_id": "mol-1",
        "molecule_name": "Example",
        "origin": "generated",
        "canonical_smiles": "CCO",
        "conformer_count": 5,
        "prepared_ligand_paths": ["prepared/ligand-1.sdf"],
        "charge_method": "gasteiger",
        "protonation_policy": "neutral pH heuristic",
        "stereochemistry_status": "specified",
        "warnings": ["Generated molecule remains a computational hypothesis."],
        "confidence": 0.6,
        "metadata": {},
    }
    payload.update(overrides)
    return Ligand3DPreparation(**payload)


def _binding_site(**overrides: Any) -> BindingSiteDefinition:
    payload: dict[str, Any] = {
        "binding_site_id": "site-1",
        "target_symbol": "LRRK2",
        "structure_id": "structure-1",
        "method": "co_crystal_ligand",
        "center": [1.0, 2.0, 3.0],
        "box_size": [18.0, 18.0, 18.0],
        "residues": ["LYS1906"],
        "reference_ligand_id": "ATP",
        "confidence": 0.75,
        "warnings": [],
        "metadata": {},
    }
    payload.update(overrides)
    return BindingSiteDefinition(**payload)


def _docking_run(**overrides: Any) -> DockingRun:
    payload: dict[str, Any] = {
        "docking_run_id": "dock-run-1",
        "target_symbol": "LRRK2",
        "structure_id": "structure-1",
        "receptor_prep_id": "receptor-prep-1",
        "binding_site_id": "site-1",
        "docking_engine": "AutoDock Vina",
        "docking_engine_version": "1.2.5",
        "config": {"exhaustiveness": 8},
        "started_at": datetime(2026, 1, 2, 12, tzinfo=UTC),
        "completed_at": datetime(2026, 1, 2, 12, 5, tzinfo=UTC),
        "status": "succeeded",
        "ligand_count": 1,
        "pose_count": 1,
        "artifacts": {"log": "artifacts/dock.log"},
        "warnings": ["Docking scores are not proof of binding."],
        "metadata": {"not_experimental_evidence": True},
    }
    payload.update(overrides)
    return DockingRun(**payload)


def _pose(**overrides: Any) -> DockingPose:
    payload: dict[str, Any] = {
        "pose_id": "pose-1",
        "docking_run_id": "dock-run-1",
        "molecule_id": "mol-1",
        "molecule_name": "Example",
        "canonical_smiles": "CCO",
        "target_symbol": "LRRK2",
        "structure_id": "structure-1",
        "binding_site_id": "site-1",
        "pose_rank": 1,
        "docking_score": 0.68,
        "score_units": "normalized_docking_score_0_1",
        "pose_path": "poses/pose-1.pdbqt",
        "interaction_summary": {"hydrogen_bonds": 1},
        "pose_quality": {"clash_check": "pass"},
        "confidence": 0.35,
        "warnings": ["Pose is not experimental evidence."],
        "metadata": {"not_experimental_evidence": True},
    }
    payload.update(overrides)
    return DockingPose(**payload)


def _interaction_profile(**overrides: Any) -> ProteinLigandInteractionProfile:
    payload: dict[str, Any] = {
        "profile_id": "profile-1",
        "pose_id": "pose-1",
        "target_symbol": "LRRK2",
        "molecule_id": "mol-1",
        "interactions": [{"interaction_type": "hydrogen_bond", "residue": "LYS1906"}],
        "interaction_counts": {"hydrogen_bond": 1},
        "key_residue_contacts": ["LYS1906"],
        "reference_similarity": 0.4,
        "warnings": ["Interactions are pose-derived computational annotations."],
        "confidence": 0.4,
        "metadata": {},
    }
    payload.update(overrides)
    return ProteinLigandInteractionProfile(**payload)


def _assessment(**overrides: Any) -> StructureAwareAssessment:
    payload: dict[str, Any] = {
        "assessment_id": "assessment-1",
        "molecule_id": "mol-1",
        "molecule_name": "Example",
        "target_symbol": "LRRK2",
        "structure_id": "structure-1",
        "docking_pose_ids": ["pose-1"],
        "structure_score": 0.7,
        "pose_confidence": 0.35,
        "interaction_score": 0.5,
        "consensus_score": 0.52,
        "applicability_domain": "suitable_experimental_structure",
        "recommendation": "retain_for_review",
        "warnings": ["Structure scores are not activity evidence."],
        "explanation": "Computational structure-aware review signal only.",
        "metadata": {},
    }
    payload.update(overrides)
    return StructureAwareAssessment(**payload)


def test_structure_schemas_round_trip_nested_workflow_payloads() -> None:
    payload = {
        "structure": _structure_record().model_dump(mode="json"),
        "selection": _selection().model_dump(mode="json"),
        "receptor": _receptor_prep().model_dump(mode="json"),
        "ligand": _ligand_prep().model_dump(mode="json"),
        "site": _binding_site().model_dump(mode="json"),
        "run": _docking_run().model_dump(mode="json"),
        "pose": _pose().model_dump(mode="json"),
        "interactions": _interaction_profile().model_dump(mode="json"),
        "assessment": _assessment().model_dump(mode="json"),
    }

    assert payload["structure"]["structure_type"] == "experimental"
    assert payload["selection"]["selected_chain_ids"] == ["A"]
    assert payload["receptor"]["missing_hydrogens_added"] is True
    assert payload["ligand"]["origin"] == "generated"
    assert payload["site"]["method"] == "co_crystal_ligand"
    assert payload["run"]["status"] == "succeeded"
    assert payload["pose"]["pose_rank"] == 1
    assert payload["interactions"]["interaction_counts"]["hydrogen_bond"] == 1
    assert payload["assessment"]["recommendation"] == "retain_for_review"


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: _selection(confidence=1.1), "confidence"),
        (lambda: _receptor_prep(confidence=-0.1), "confidence"),
        (lambda: _ligand_prep(confidence=1.01), "confidence"),
        (lambda: _binding_site(confidence=1.5), "confidence"),
        (lambda: _pose(confidence=-0.01), "confidence"),
        (lambda: _interaction_profile(reference_similarity=1.2), "reference_similarity"),
        (lambda: _interaction_profile(confidence=1.2), "confidence"),
        (lambda: _assessment(structure_score=1.2), "structure_score"),
        (lambda: _assessment(pose_confidence=-0.1), "pose_confidence"),
        (lambda: _assessment(interaction_score=1.2), "interaction_score"),
        (lambda: _assessment(consensus_score=-0.1), "consensus_score"),
    ],
)
def test_structure_schema_scores_and_confidences_are_bounded(factory, field_name: str) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: _structure_record(structure_type="computed"), "structure_type"),
        (lambda: _ligand_prep(origin="screening"), "origin"),
        (lambda: _ligand_prep(stereochemistry_status="unknown"), "stereochemistry_status"),
        (lambda: _binding_site(method="invented_site"), "method"),
        (lambda: _docking_run(status="complete"), "status"),
        (lambda: _assessment(applicability_domain="excellent"), "applicability_domain"),
        (lambda: _assessment(recommendation="advance"), "recommendation"),
    ],
)
def test_structure_schema_literals_reject_unknown_values(factory, field_name: str) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: _structure_record(retrieved_at=datetime(2026, 1, 2)), "retrieved_at"),
        (lambda: _docking_run(started_at=datetime(2026, 1, 2, 12)), "started_at"),
        (lambda: _docking_run(completed_at=datetime(2026, 1, 2, 12, 5)), "completed_at"),
    ],
)
def test_structure_schema_timestamps_must_be_timezone_aware(
    factory,
    field_name: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)
    assert "timezone-aware" in str(error.value)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _docking_run(metadata={"claim": "This docking run is experimental evidence."}),
        lambda: _pose(interaction_summary={"claim": "This pose proves binding."}),
        lambda: _pose(metadata={"claim": "Pose is activity evidence."}),
    ],
)
def test_docking_and_pose_objects_reject_experimental_evidence_claims(factory) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert "experimental evidence" in str(error.value) or "binding/activity claims" in str(
        error.value
    )


def test_binding_site_box_vectors_must_have_three_coordinates() -> None:
    with pytest.raises(ValidationError) as error:
        _binding_site(center=[1.0, 2.0], box_size=[18.0, 18.0, 18.0, 18.0])

    assert "center" in str(error.value)
    assert "box_size" in str(error.value)
