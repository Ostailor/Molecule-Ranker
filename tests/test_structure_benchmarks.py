from __future__ import annotations

import json
from datetime import UTC, datetime

from molecule_ranker.structure.benchmarks import StructureBenchmarkHarness
from molecule_ranker.structure.schemas import (
    DockingRun,
    Ligand3DPreparation,
    ReceptorPreparation,
    StructureAwareAssessment,
    StructureRecord,
    StructureSelection,
)


def _structure(structure_id: str, target: str, structure_type: str = "experimental"):
    return StructureRecord(
        structure_id=structure_id,
        source="RCSB_PDB" if structure_type == "experimental" else "AlphaFold_DB",
        external_id=structure_id,
        target_symbol=target,
        target_identifiers={"uniprot": f"{target}-UP"},
        structure_type=structure_type,  # type: ignore[arg-type]
        experimental_method="X-ray diffraction" if structure_type == "experimental" else None,
        resolution_angstrom=2.1 if structure_type == "experimental" else None,
        coverage={"chain_A": 0.9},
        chains=["A"],
        ligands=[{"ligand_id": "ATP", "role": "co_crystal"}]
        if structure_id.endswith("1")
        else [],
        mutations=[],
        organism="Homo sapiens",
        release_date="2020-01-02",
        quality_metrics={},
        url=f"https://example.org/{structure_id}",
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        metadata={},
    )


def _selection(selection_id: str, target: str, structure_id: str, confidence: float):
    return StructureSelection(
        selection_id=selection_id,
        target_symbol=target,
        selected_structure_id=structure_id,
        selected_chain_ids=["A"],
        selection_reason="Synthetic benchmark selection.",
        confidence=confidence,
        rejected_structures=[],
        warnings=[],
        metadata={},
    )


def _receptor(prep_id: str, structure_id: str, confidence: float = 0.7):
    return ReceptorPreparation(
        receptor_prep_id=prep_id,
        structure_id=structure_id,
        target_symbol="LRRK2",
        input_structure_path="inputs/receptor.pdb",
        prepared_receptor_path="prepared/receptor.pdbqt" if confidence > 0 else None,
        preparation_method="metadata_only",
        protonation_policy="unchanged",
        kept_chains=["A"],
        removed_chains=[],
        kept_heterogens=[],
        removed_heterogens=[],
        missing_atoms_fixed=False,
        missing_hydrogens_added=False,
        missing_loops_modeled=False,
        alternate_locations_resolved=False,
        warnings=[] if confidence > 0 else ["preparation failed"],
        confidence=confidence,
        metadata={},
    )


def _ligand(ligand_id: str, paths: list[str] | None = None):
    paths = paths if paths is not None else [f"prepared/{ligand_id}.sdf"]
    return Ligand3DPreparation(
        ligand_prep_id=f"prep-{ligand_id}",
        molecule_id=ligand_id,
        molecule_name=ligand_id,
        origin="generated",
        canonical_smiles="CCO",
        conformer_count=len(paths),
        prepared_ligand_paths=paths,
        charge_method=None,
        protonation_policy="neutral pH heuristic",
        stereochemistry_status="specified",
        warnings=[],
        confidence=0.7 if paths else 0.0,
        metadata={},
    )


def _docking(run_id: str, status: str, ligand_count: int = 2):
    return DockingRun(
        docking_run_id=run_id,
        target_symbol="LRRK2",
        structure_id="s1",
        receptor_prep_id="prep-s1",
        binding_site_id="site-1",
        docking_engine="NullDockingEngine",
        docking_engine_version=None,
        config={"max_docked_ligands": 3},
        started_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        completed_at=datetime(2026, 1, 2, 12, 5, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        ligand_count=ligand_count,
        pose_count=ligand_count if status == "succeeded" else 0,
        artifacts={},
        warnings=["Docking scores are not proof of binding."],
        metadata={"not_experimental_evidence": True},
    )


def _assessment(
    molecule_id: str,
    *,
    consensus: float,
    recommendation: str = "retain_for_review",
    warnings: list[str] | None = None,
):
    return StructureAwareAssessment(
        assessment_id=f"assessment-{molecule_id}",
        molecule_id=molecule_id,
        molecule_name=molecule_id,
        target_symbol="LRRK2",
        structure_id="s1",
        docking_pose_ids=[f"pose-{molecule_id}"],
        structure_score=0.7,
        pose_confidence=0.6,
        interaction_score=0.5,
        consensus_score=consensus,
        applicability_domain="suitable_experimental_structure",
        recommendation=recommendation,  # type: ignore[arg-type]
        warnings=warnings or ["Structure scores are not activity evidence."],
        explanation="Computational structure-aware review only.",
        metadata={"pose_qc_status": "reject" if recommendation == "reject" else "pass"},
    )


def test_benchmark_computes_metrics(tmp_path) -> None:
    report = StructureBenchmarkHarness().benchmark_artifact(
        {
            "target_symbols": ["LRRK2", "MAOB"],
            "structures": [
                _structure("s1", "LRRK2").model_dump(mode="json"),
                _structure("s2", "LRRK2", "predicted").model_dump(mode="json"),
                _structure("s3", "MAOB").model_dump(mode="json"),
            ],
            "selections": [
                _selection("sel-1", "LRRK2", "s1", 0.8).model_dump(mode="json"),
                _selection("sel-2", "MAOB", "s3", 0.6).model_dump(mode="json"),
            ],
            "receptor_preparations": [
                _receptor("prep-s1", "s1").model_dump(mode="json"),
                _receptor("prep-s2", "s2", confidence=0.0).model_dump(mode="json"),
            ],
            "ligand_preparations": [
                _ligand("mol-1").model_dump(mode="json"),
                _ligand("mol-2", paths=[]).model_dump(mode="json"),
            ],
            "docking_runs": [
                _docking("dock-1", "succeeded").model_dump(mode="json"),
                _docking("dock-2", "failed").model_dump(mode="json"),
            ],
            "structure_assessments": [
                _assessment("mol-1", consensus=0.7).model_dump(mode="json"),
                _assessment(
                    "mol-2",
                    consensus=0.2,
                    recommendation="reject",
                    warnings=["Rejected due to pose QC."],
                ).model_dump(mode="json"),
            ],
        },
        output_dir=tmp_path,
    )

    metrics = report.metrics
    assert metrics.structures_found_per_target == {"LRRK2": 2, "MAOB": 1}
    assert metrics.receptor_prep_success_rate == 0.5
    assert metrics.ligand_prep_success_rate == 0.5
    assert metrics.docking_success_rate == 0.5
    assert metrics.pose_qc_pass_rate == 0.5
    assert metrics.generated_molecules_with_structure_assessment == 2
    assert metrics.rejected_due_to_pose_qc == 1
    assert metrics.predicted_vs_experimental_structure_usage == {
        "experimental": 2,
        "predicted": 0,
        "user_supplied": 0,
        "homology_model": 0,
        "unavailable": 0,
    }
    assert metrics.docking_budget_usage["ligands_docked"] == 4
    assert (tmp_path / "structure_benchmark_report.json").exists()
    assert (tmp_path / "structure_benchmark_report.md").exists()
    saved = json.loads((tmp_path / "structure_benchmark_report.json").read_text())
    assert saved["metrics"]["generated_molecules_with_structure_assessment"] == 2


def test_empty_structure_set_handled(tmp_path) -> None:
    report = StructureBenchmarkHarness().benchmark_artifact({}, output_dir=tmp_path)

    assert report.metrics.structures_found_per_target == {}
    assert report.metrics.receptor_prep_success_rate == 0.0
    assert report.metrics.consensus_score_distribution["count"] == 0
    assert "empty_structure_benchmark_artifact" in report.warnings
    assert (tmp_path / "structure_benchmark_report.md").exists()


def test_redocking_benchmark_skipped_if_no_reference_ligand() -> None:
    report = StructureBenchmarkHarness().benchmark_artifact(
        {
            "target_symbols": ["LRRK2"],
            "structures": [_structure("s-no-ligand", "LRRK2").model_dump(mode="json")],
        },
        config={"enable_redocking_benchmark": True},
    )

    redocking = report.optional_benchmarks["redocking"]
    assert redocking["status"] == "skipped"
    assert redocking["reason"] == "no_reference_ligand"
    assert redocking["requires_live_pdb"] is False
