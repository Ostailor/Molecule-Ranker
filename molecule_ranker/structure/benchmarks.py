from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import (
    DockingRun,
    Ligand3DPreparation,
    ReceptorPreparation,
    StructureAwareAssessment,
    StructureRecord,
    StructureSelection,
)


class StructureBenchmarkMetrics(BaseModel):
    structures_found_per_target: dict[str, int] = Field(default_factory=dict)
    selected_structure_confidence_distribution: dict[str, Any] = Field(default_factory=dict)
    receptor_prep_success_rate: float = Field(ge=0.0, le=1.0)
    ligand_prep_success_rate: float = Field(ge=0.0, le=1.0)
    docking_success_rate: float = Field(ge=0.0, le=1.0)
    pose_qc_pass_rate: float = Field(ge=0.0, le=1.0)
    consensus_score_distribution: dict[str, Any] = Field(default_factory=dict)
    predicted_vs_experimental_structure_usage: dict[str, int] = Field(default_factory=dict)
    generated_molecules_with_structure_assessment: int = Field(ge=0)
    docking_budget_usage: dict[str, Any] = Field(default_factory=dict)
    rejected_due_to_pose_qc: int = Field(ge=0)


class StructureBenchmarkReport(BaseModel):
    benchmark_name: str = "structure_workflow_v1_3"
    metrics: StructureBenchmarkMetrics
    optional_benchmarks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructureBenchmarkHarness:
    """Synthetic/default benchmark harness for optional structure workflows."""

    def benchmark_file(
        self,
        path: str | Path,
        *,
        output_dir: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> StructureBenchmarkReport:
        payload = json.loads(Path(path).read_text())
        if not isinstance(payload, Mapping):
            raise ValueError("Structure benchmark artifact must be a JSON object.")
        return self.benchmark_artifact(payload, output_dir=output_dir, config=config)

    def benchmark_artifact(
        self,
        artifact: Mapping[str, Any],
        *,
        output_dir: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> StructureBenchmarkReport:
        structures = _records(artifact.get("structures"), StructureRecord)
        selections = _records(artifact.get("selections"), StructureSelection)
        receptor_preps = _records(
            artifact.get("receptor_preparations"),
            ReceptorPreparation,
        )
        ligand_preps = _records(
            artifact.get("ligand_preparations"),
            Ligand3DPreparation,
        )
        docking_runs = _records(artifact.get("docking_runs"), DockingRun)
        assessments = _records(
            artifact.get("structure_assessments")
            or artifact.get("structure_aware_assessments"),
            StructureAwareAssessment,
        )
        target_symbols = _target_symbols(artifact, structures, selections)
        metrics = StructureBenchmarkMetrics(
            structures_found_per_target=_structures_found_per_target(
                target_symbols,
                structures,
            ),
            selected_structure_confidence_distribution=_distribution(
                [selection.confidence for selection in selections]
            ),
            receptor_prep_success_rate=_rate(
                sum(1 for prep in receptor_preps if _receptor_success(prep)),
                len(receptor_preps),
            ),
            ligand_prep_success_rate=_rate(
                sum(1 for prep in ligand_preps if _ligand_success(prep)),
                len(ligand_preps),
            ),
            docking_success_rate=_rate(
                sum(1 for run in docking_runs if run.status == "succeeded"),
                len(docking_runs),
            ),
            pose_qc_pass_rate=_rate(
                sum(1 for assessment in assessments if _pose_qc_passed(assessment)),
                len(assessments),
            ),
            consensus_score_distribution=_distribution(
                [assessment.consensus_score for assessment in assessments]
            ),
            predicted_vs_experimental_structure_usage=_structure_usage(
                structures,
                selections,
            ),
            generated_molecules_with_structure_assessment=len(
                {assessment.molecule_id for assessment in assessments}
            ),
            docking_budget_usage=_docking_budget_usage(docking_runs),
            rejected_due_to_pose_qc=sum(
                1 for assessment in assessments if _rejected_due_to_pose_qc(assessment)
            ),
        )
        report = StructureBenchmarkReport(
            metrics=metrics,
            optional_benchmarks={
                "redocking": _redocking_benchmark(
                    structures,
                    config=dict(config or {}),
                )
            },
            warnings=_warnings(
                structures=structures,
                selections=selections,
                receptor_preps=receptor_preps,
                ligand_preps=ligand_preps,
                docking_runs=docking_runs,
                assessments=assessments,
            ),
            metadata={
                "requires_live_pdb": False,
                "default_tests_use_mocked_or_synthetic_structures": True,
                "claim_boundary": (
                    "structure workflow benchmarks are computational reproducibility "
                    "and coverage metrics, not evidence of binding, activity, safety, "
                    "or efficacy"
                ),
            },
        )
        if output_dir is not None:
            self._write_reports(report, Path(output_dir))
        return report

    def _write_reports(self, report: StructureBenchmarkReport, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "structure_benchmark_report.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
        )
        (output_dir / "structure_benchmark_report.md").write_text(
            _markdown_report(report)
        )


def _records(value: Any, model_type: type[BaseModel]) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    records: list[Any] = []
    for item in value:
        if isinstance(item, model_type):
            records.append(item)
        elif isinstance(item, Mapping):
            records.append(model_type.model_validate(dict(item)))
    return records


def _target_symbols(
    artifact: Mapping[str, Any],
    structures: Sequence[StructureRecord],
    selections: Sequence[StructureSelection],
) -> list[str]:
    explicit = artifact.get("target_symbols")
    symbols: set[str] = set()
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        symbols.update(str(item) for item in explicit if str(item).strip())
    symbols.update(structure.target_symbol for structure in structures)
    symbols.update(selection.target_symbol for selection in selections)
    return sorted(symbols)


def _structures_found_per_target(
    target_symbols: Sequence[str],
    structures: Sequence[StructureRecord],
) -> dict[str, int]:
    return {
        target: sum(1 for structure in structures if structure.target_symbol == target)
        for target in target_symbols
    }


def _receptor_success(prep: ReceptorPreparation) -> bool:
    return bool(prep.prepared_receptor_path) and prep.confidence > 0.0


def _ligand_success(prep: Ligand3DPreparation) -> bool:
    return bool(prep.prepared_ligand_paths) and prep.conformer_count > 0 and prep.confidence > 0.0


def _pose_qc_passed(assessment: StructureAwareAssessment) -> bool:
    status = str(assessment.metadata.get("pose_qc_status") or "").lower()
    if status:
        return status == "pass"
    if assessment.recommendation == "reject":
        return False
    return assessment.pose_confidence >= 0.5


def _rejected_due_to_pose_qc(assessment: StructureAwareAssessment) -> bool:
    if assessment.recommendation != "reject":
        return False
    status = str(assessment.metadata.get("pose_qc_status") or "").lower()
    warning_text = " ".join(assessment.warnings).lower()
    return status == "reject" or "pose qc" in warning_text or "pose_qc" in warning_text


def _structure_usage(
    structures: Sequence[StructureRecord],
    selections: Sequence[StructureSelection],
) -> dict[str, int]:
    usage = {
        "experimental": 0,
        "predicted": 0,
        "user_supplied": 0,
        "homology_model": 0,
        "unavailable": 0,
    }
    by_id = {structure.structure_id: structure for structure in structures}
    for selection in selections:
        selected = by_id.get(selection.selected_structure_id)
        if selected is None:
            usage["unavailable"] += 1
        else:
            usage[selected.structure_type] += 1
    return usage


def _docking_budget_usage(docking_runs: Sequence[DockingRun]) -> dict[str, Any]:
    ligands_docked = sum(run.ligand_count for run in docking_runs)
    poses_generated = sum(run.pose_count for run in docking_runs)
    configured_budget = sum(_configured_ligand_budget(run) for run in docking_runs)
    return {
        "runs": len(docking_runs),
        "ligands_docked": ligands_docked,
        "poses_generated": poses_generated,
        "configured_ligand_budget": configured_budget,
        "budget_fraction_used": _rate(ligands_docked, configured_budget),
    }


def _configured_ligand_budget(run: DockingRun) -> int:
    for key in ("max_docked_ligands", "max_ligands", "ligand_budget"):
        value = run.config.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return run.ligand_count


def _redocking_benchmark(
    structures: Sequence[StructureRecord],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    if not bool(config.get("enable_redocking_benchmark", False)):
        return {
            "status": "disabled",
            "requires_live_pdb": False,
        }
    reference_structures = [
        structure for structure in structures if bool(structure.ligands)
    ]
    if not reference_structures:
        return {
            "status": "skipped",
            "reason": "no_reference_ligand",
            "requires_live_pdb": False,
        }
    return {
        "status": "available",
        "reference_structure_ids": [
            structure.structure_id for structure in reference_structures
        ],
        "reference_ligand_count": sum(
            len(structure.ligands) for structure in reference_structures
        ),
        "requires_live_pdb": False,
        "note": "Synthetic benchmark reports availability only; no live redocking is run.",
    }


def _warnings(
    *,
    structures: Sequence[StructureRecord],
    selections: Sequence[StructureSelection],
    receptor_preps: Sequence[ReceptorPreparation],
    ligand_preps: Sequence[Ligand3DPreparation],
    docking_runs: Sequence[DockingRun],
    assessments: Sequence[StructureAwareAssessment],
) -> list[str]:
    warnings = [
        "benchmark_is_not_experimental_evidence",
        "docking_scores_are_not_binding_evidence",
        "poses_are_not_experimental_evidence",
    ]
    if not any((structures, selections, receptor_preps, ligand_preps, docking_runs, assessments)):
        warnings.append("empty_structure_benchmark_artifact")
    if not structures:
        warnings.append("no_structures_found")
    if not assessments:
        warnings.append("no_structure_aware_assessments")
    return warnings


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    bounded = [max(0.0, min(float(value), 1.0)) for value in values]
    if not bounded:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
        }
    ordered = sorted(bounded)
    midpoint = len(ordered) // 2
    median = (
        ordered[midpoint]
        if len(ordered) % 2 == 1
        else (ordered[midpoint - 1] + ordered[midpoint]) / 2
    )
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "mean": round(sum(ordered) / len(ordered), 3),
        "median": round(median, 3),
    }


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(numerator / denominator, 1.0)), 3)


def _markdown_report(report: StructureBenchmarkReport) -> str:
    metrics = report.metrics
    lines = [
        "# Structure Workflow Benchmark Report",
        "",
        "- Benchmark: structure_workflow_v1_3",
        "- Claim boundary: computational workflow quality metrics only.",
        "- Docking scores are not proof of binding.",
        "- Poses are not experimental evidence.",
        "",
        "## Metrics",
        "",
        f"- Structures found per target: {metrics.structures_found_per_target}",
        (
            "- Selected confidence distribution: "
            f"{metrics.selected_structure_confidence_distribution}"
        ),
        f"- Receptor prep success rate: {metrics.receptor_prep_success_rate:.3f}",
        f"- Ligand prep success rate: {metrics.ligand_prep_success_rate:.3f}",
        f"- Docking success rate: {metrics.docking_success_rate:.3f}",
        f"- Pose QC pass rate: {metrics.pose_qc_pass_rate:.3f}",
        f"- Consensus score distribution: {metrics.consensus_score_distribution}",
        (
            "- Predicted vs experimental usage: "
            f"{metrics.predicted_vs_experimental_structure_usage}"
        ),
        (
            "- Generated molecules with structure assessment: "
            f"{metrics.generated_molecules_with_structure_assessment}"
        ),
        f"- Docking budget usage: {metrics.docking_budget_usage}",
        f"- Rejected due to pose QC: {metrics.rejected_due_to_pose_qc}",
        "",
        "## Optional Benchmarks",
        "",
        f"- Redocking: {report.optional_benchmarks.get('redocking', {})}",
    ]
    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines)


__all__ = [
    "StructureBenchmarkHarness",
    "StructureBenchmarkMetrics",
    "StructureBenchmarkReport",
]
