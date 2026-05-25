from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.developability.docking import (
    BindingSite,
    DockingEngine,
    DockingUnavailableError,
    VinaAdapter,
)
from molecule_ranker.developability.schemas import DockingAssessment
from molecule_ranker.developability.structure import TargetStructureRecord


def _structure() -> TargetStructureRecord:
    return TargetStructureRecord(
        target_symbol="LRRK2",
        structure_id="6XYZ",
        source="RCSB PDB",
        structure_kind="experimental",
        method="X-RAY DIFFRACTION",
        resolution=1.8,
        chains=["A"],
        ligands=["ATP"],
        uniprot_accessions=["Q5S007"],
        confidence=0.85,
    )


def _binding_site() -> BindingSite:
    return BindingSite(
        method="known_ligand_site",
        center_x=1.0,
        center_y=2.0,
        center_z=3.0,
        size_x=18.0,
        size_y=18.0,
        size_z=18.0,
        reference_ligand_id="ATP",
        confidence=0.7,
    )


def test_disabled_docking_skips_cleanly():
    assessment = VinaAdapter(executable_resolver=lambda _: None).dock(
        "CCO",
        _structure(),
        _binding_site(),
        {"enable_docking": False, "metadata": {"ligand_id": "lig-1"}},
    )

    assert assessment.enabled is False
    assert assessment.docking_score is None
    assert "disabled" in " ".join(assessment.warnings).lower()
    assert assessment.metadata["docking_performed"] is False


def test_missing_vina_warns_or_fails_by_config():
    adapter = VinaAdapter(executable_resolver=lambda _: None)

    warned = adapter.dock(
        "CCO",
        _structure(),
        _binding_site(),
        {"enable_docking": True, "strict_structure_mode": False},
    )

    assert warned.enabled is False
    assert any("unavailable" in warning.lower() for warning in warned.warnings)

    with pytest.raises(DockingUnavailableError, match="AutoDock Vina executable"):
        adapter.dock(
            "CCO",
            _structure(),
            _binding_site(),
            {"enable_docking": True, "strict_structure_mode": True},
        )


def test_docking_result_schema_works_with_mocked_engine(tmp_path: Path):
    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    receptor.write_text("RECEPTOR\n")
    ligand.write_text("LIGAND\n")

    def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(list(command), 0, "AutoDock Vina 1.2.5", "")
        return subprocess.CompletedProcess(
            list(command),
            0,
            "-----+------------+----------+----------\n    1       -8.4      0.000      0.000\n",
            "",
        )

    assessment = VinaAdapter(
        runner=runner,
        executable_resolver=lambda executable: executable,
    ).dock(
        "CCO",
        _structure(),
        _binding_site(),
        {
            "enable_docking": True,
            "prepared_receptor_path": str(receptor),
            "prepared_ligand_path": str(ligand),
            "write_docking_artifacts": False,
            "metadata": {"ligand_id": "lig-1"},
        },
    )

    assert assessment.enabled is True
    assert assessment.docking_engine == "AutoDock Vina"
    assert assessment.structure_id == "6XYZ"
    assert assessment.binding_site_method == "known_ligand_site"
    assert assessment.docking_score == pytest.approx(0.7)
    assert assessment.score_units == "normalized_vina_affinity_0_1"
    assert assessment.pose_file is None
    assert assessment.metadata["raw_docking_score"] == -8.4
    assert assessment.metadata["raw_score_units"] == "kcal/mol"
    assert assessment.metadata["engine_version"] == "AutoDock Vina 1.2.5"
    assert assessment.metadata["pose_file_written"] is False


def test_docking_score_does_not_create_evidence_claim():
    class MockEngine:
        engine_name = "mock"

        def dock(
            self,
            ligand_smiles: str,
            structure: Any,
            binding_site: Any,
            config: Any,
        ) -> DockingAssessment:
            return DockingAssessment(
                enabled=True,
                target_symbol="LRRK2",
                structure_source="RCSB PDB",
                structure_id="6XYZ",
                ligand_id="lig-1",
                docking_engine=self.engine_name,
                docking_score=0.7,
                score_units="normalized_mock_score",
                binding_site_method="user_supplied_box",
                confidence=0.3,
                warnings=[
                    "Docking score is a weak computational heuristic and does not prove binding."
                ],
                metadata={"no_evidence_claim_created": True},
            )

    engine: DockingEngine = MockEngine()
    assessment = engine.dock("CCO", _structure(), _binding_site(), {})
    payload = assessment.model_dump()

    assert assessment.metadata["no_evidence_claim_created"] is True
    assert "evidence" not in payload
    assert "does not prove binding" in " ".join(assessment.warnings)
