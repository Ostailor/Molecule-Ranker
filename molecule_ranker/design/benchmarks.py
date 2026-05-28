from __future__ import annotations

import importlib.util
import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity


class DesignBenchmarkMetrics(BaseModel):
    generated_count: int = Field(ge=0)
    retained_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    validity_rate: float = Field(ge=0.0, le=1.0)
    uniqueness_rate: float = Field(ge=0.0, le=1.0)
    novelty_rate: float = Field(ge=0.0, le=1.0)
    diversity: float = Field(ge=0.0, le=1.0)
    scaffold_diversity: float = Field(ge=0.0, le=1.0)
    near_duplicate_rate: float = Field(ge=0.0, le=1.0)
    developability_pass_rate: float = Field(ge=0.0, le=1.0)
    critical_alert_rate: float = Field(ge=0.0, le=1.0)
    experiment_readiness_distribution: dict[str, Any] = Field(default_factory=dict)
    oracle_score_distribution: dict[str, Any] = Field(default_factory=dict)
    uncertainty_distribution: dict[str, Any] = Field(default_factory=dict)
    generator_contribution: dict[str, dict[str, Any]] = Field(default_factory=dict)
    generation_cost_per_retained_candidate: float = Field(ge=0.0)
    active_learning_sample_efficiency: dict[str, Any] = Field(default_factory=dict)


class DesignBenchmarkReport(BaseModel):
    benchmark_name: str = "internal_design_generation_v1_1"
    random_seed: int
    metrics: DesignBenchmarkMetrics
    optional_modes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DesignBenchmarkHarness:
    """V1.1 generated-molecule benchmark harness with optional external modes."""

    def __init__(self, *, random_seed: int = 13) -> None:
        self.random_seed = random_seed
        self._rng = random.Random(random_seed)

    def benchmark_file(
        self,
        path: str | Path,
        *,
        output_dir: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> DesignBenchmarkReport:
        payload = json.loads(Path(path).read_text())
        if not isinstance(payload, dict):
            raise ValueError("Generated benchmark artifact must be a JSON object.")
        return self.benchmark_artifact(payload, output_dir=output_dir, config=config)

    def benchmark_artifact(
        self,
        artifact: Mapping[str, Any],
        *,
        output_dir: str | Path | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> DesignBenchmarkReport:
        config = dict(config or {})
        retained = _retained_records(artifact)
        rejected = _rejected_records(artifact)
        records = [*retained, *rejected]
        generated_count = _nonnegative_int(
            artifact.get("generated_count"),
            fallback=len(records),
        )
        retained_count = _nonnegative_int(
            artifact.get("retained_count"),
            fallback=len(retained),
        )
        rejected_count = _nonnegative_int(
            artifact.get("rejected_count"),
            fallback=len(rejected),
        )
        metrics = self._metrics(
            artifact=artifact,
            records=records,
            retained=retained,
            rejected=rejected,
            generated_count=generated_count,
            retained_count=retained_count,
            rejected_count=rejected_count,
        )
        report = DesignBenchmarkReport(
            random_seed=self.random_seed,
            metrics=metrics,
            optional_modes=self._optional_modes(
                artifact=artifact,
                records=records,
                config=config,
            ),
            warnings=self._warnings(records, retained_count),
            metadata={
                "reproducible_random_seed": self.random_seed,
                "external_dependencies_required": False,
                "claim_boundary": (
                    "benchmark metrics are computational quality signals, not "
                    "evidence of activity, safety, or synthesizability"
                ),
            },
        )
        if output_dir is not None:
            self._write_reports(report, Path(output_dir))
        return report

    def _metrics(
        self,
        *,
        artifact: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        retained: Sequence[Mapping[str, Any]],
        rejected: Sequence[Mapping[str, Any]],
        generated_count: int,
        retained_count: int,
        rejected_count: int,
    ) -> DesignBenchmarkMetrics:
        if generated_count == 0:
            return DesignBenchmarkMetrics(
                generated_count=0,
                retained_count=0,
                rejected_count=0,
                validity_rate=0.0,
                uniqueness_rate=0.0,
                novelty_rate=0.0,
                diversity=0.0,
                scaffold_diversity=0.0,
                near_duplicate_rate=0.0,
                developability_pass_rate=0.0,
                critical_alert_rate=0.0,
                experiment_readiness_distribution=_distribution([]),
                oracle_score_distribution=_distribution([]),
                uncertainty_distribution=_distribution([]),
                generator_contribution={},
                generation_cost_per_retained_candidate=0.0,
                active_learning_sample_efficiency=_active_learning_efficiency(
                    artifact,
                    retained,
                ),
            )
        return DesignBenchmarkMetrics(
            generated_count=generated_count,
            retained_count=retained_count,
            rejected_count=rejected_count,
            validity_rate=_rate(sum(1 for record in records if _valid(record)), generated_count),
            uniqueness_rate=_uniqueness_rate(retained),
            novelty_rate=_novelty_rate(records),
            diversity=_diversity(retained),
            scaffold_diversity=_scaffold_diversity(retained),
            near_duplicate_rate=_near_duplicate_rate(records),
            developability_pass_rate=_developability_pass_rate(retained),
            critical_alert_rate=_rate(
                sum(1 for record in records if _critical_alert(record)),
                generated_count,
            ),
            experiment_readiness_distribution=_readiness_distribution(retained),
            oracle_score_distribution=_distribution(_oracle_scores(retained)),
            uncertainty_distribution=_distribution(_uncertainty_scores(retained)),
            generator_contribution=_generator_contribution(retained, rejected),
            generation_cost_per_retained_candidate=_cost_per_retained(
                artifact,
                retained_count,
            ),
            active_learning_sample_efficiency=_active_learning_efficiency(
                artifact,
                retained,
            ),
        )

    def _optional_modes(
        self,
        *,
        artifact: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {
            "guacamol": _guacamol_mode(config),
            "pmo": _pmo_mode(artifact, records, config),
            "internal_target_conditioned": _internal_target_conditioned_mode(
                records,
                config,
            ),
        }

    def _warnings(
        self,
        records: Sequence[Mapping[str, Any]],
        retained_count: int,
    ) -> list[str]:
        warnings = [
            "benchmark_is_not_experimental_evidence",
            "generated_molecules_remain_hypotheses",
        ]
        if not records:
            warnings.append("empty_generation_artifact")
        if retained_count == 0:
            warnings.append("no_retained_candidates")
        return warnings

    def _write_reports(self, report: DesignBenchmarkReport, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "benchmark_report.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
        )
        (output_dir / "benchmark_report.md").write_text(_markdown_report(report))


def _retained_records(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    retained = artifact.get("retained_generated_molecules")
    if isinstance(retained, list):
        return [_as_record(item) for item in retained if isinstance(item, Mapping)]
    legacy = artifact.get("generated_molecule_hypotheses")
    if isinstance(legacy, list):
        return [_as_record(item) for item in legacy if isinstance(item, Mapping)]
    return []


def _rejected_records(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    rejected = artifact.get("rejected_generated_molecules")
    if not isinstance(rejected, list):
        return []
    records: list[dict[str, Any]] = []
    for item in rejected:
        if not isinstance(item, Mapping):
            continue
        generated = item.get("generated_molecule", item)
        if isinstance(generated, Mapping):
            record = _as_record(generated)
            reasons = item.get("rejection_reasons")
            if isinstance(reasons, list):
                record["benchmark_rejection_reasons"] = list(reasons)
            records.append(record)
    return records


def _as_record(value: Mapping[str, Any]) -> dict[str, Any]:
    return dict(value)


def _valid(record: Mapping[str, Any]) -> bool:
    validation = _mapping(record.get("validation"))
    if validation.get("valid_rdkit_mol") is False:
        return False
    smiles = _smiles(record)
    return smiles is not None and mol_from_smiles(smiles) is not None


def _smiles(record: Mapping[str, Any]) -> str | None:
    value = record.get("canonical_smiles") or record.get("smiles")
    return str(value) if value not in (None, "") else None


def _uniqueness_rate(records: Sequence[Mapping[str, Any]]) -> float:
    smiles = [_smiles(record) for record in records if _smiles(record)]
    return _rate(len(set(smiles)), len(smiles))


def _novelty_rate(records: Sequence[Mapping[str, Any]]) -> float:
    return _rate(
        sum(
            1
            for record in records
            if _novelty_class(record) in {"close_analog", "novel_analog", "distant"}
        ),
        len(records),
    )


def _near_duplicate_rate(records: Sequence[Mapping[str, Any]]) -> float:
    return _rate(
        sum(
            1
            for record in records
            if _novelty_class(record) in {"duplicate", "near_duplicate"}
        ),
        len(records),
    )


def _novelty_class(record: Mapping[str, Any]) -> str:
    novelty = _mapping(record.get("novelty"))
    return str(novelty.get("novelty_class") or "unknown")


def _diversity(records: Sequence[Mapping[str, Any]]) -> float:
    mols = [
        mol
        for record in records
        if (smiles := _smiles(record)) is not None
        if (mol := mol_from_smiles(smiles)) is not None
    ]
    if len(mols) < 2:
        return 0.0
    distances: list[float] = []
    for index, mol in enumerate(mols):
        for other in mols[index + 1 :]:
            distances.append(1.0 - tanimoto_similarity(mol, other))
    return round(sum(distances) / len(distances), 3) if distances else 0.0


def _scaffold_diversity(records: Sequence[Mapping[str, Any]]) -> float:
    scaffolds = [
        scaffold
        for record in records
        if (scaffold := _scaffold_id(record)) not in (None, "")
    ]
    return _rate(len(set(scaffolds)), len(scaffolds))


def _scaffold_id(record: Mapping[str, Any]) -> str | None:
    metadata = _mapping(record.get("metadata"))
    value = metadata.get("scaffold_id") or record.get("scaffold_id") or record.get(
        "diversity_cluster"
    )
    return str(value) if value not in (None, "") else None


def _developability_pass_rate(records: Sequence[Mapping[str, Any]]) -> float:
    if not records:
        return 0.0
    return _rate(sum(1 for record in records if _developability_pass(record)), len(records))


def _developability_pass(record: Mapping[str, Any]) -> bool:
    assessment = _mapping(record.get("developability_assessment"))
    metadata = _mapping(assessment.get("metadata"))
    score = assessment.get("developability_score")
    recommendation = str(assessment.get("triage_recommendation") or "")
    risk_level = str(metadata.get("risk_level") or "").lower()
    if risk_level == "critical" or recommendation == "high_risk_flags":
        return False
    if isinstance(score, (int, float)):
        return float(score) >= 0.5
    score_breakdown = _mapping(record.get("score_breakdown"))
    score = score_breakdown.get("developability_score")
    return bool(isinstance(score, (int, float)) and float(score) >= 0.5)


def _critical_alert(record: Mapping[str, Any]) -> bool:
    validation = _mapping(record.get("validation"))
    if validation.get("rejection_reasons"):
        return True
    alerts = validation.get("pains_or_alerts")
    if isinstance(alerts, list) and any("critical" in str(item).lower() for item in alerts):
        return True
    oracle = _mapping(_mapping(record.get("metadata")).get("oracle_scoring"))
    flags = oracle.get("risk_flags")
    return bool(
        isinstance(flags, list) and any("critical" in str(flag).lower() for flag in flags)
    )


def _readiness_distribution(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    scores: list[float] = []
    for record in records:
        readiness = _mapping(_mapping(record.get("metadata")).get("experiment_readiness"))
        bucket = readiness.get("bucket") or readiness.get("label")
        if bucket not in (None, ""):
            buckets[str(bucket)] = buckets.get(str(bucket), 0) + 1
        score = readiness.get("score")
        if not isinstance(score, (int, float)):
            score = _mapping(record.get("score_breakdown")).get("experiment_readiness_score")
        if isinstance(score, (int, float)):
            scores.append(float(score))
    return {"buckets": dict(sorted(buckets.items())), "scores": _distribution(scores)}


def _oracle_score(record: Mapping[str, Any]) -> float | None:
    oracle = _mapping(_mapping(record.get("metadata")).get("oracle_scoring"))
    value = oracle.get("experiment_worthiness_score")
    if isinstance(value, (int, float)):
        return float(value)
    value = _mapping(record.get("score_breakdown")).get("final_generation_score")
    return float(value) if isinstance(value, (int, float)) else None


def _oracle_scores(records: Sequence[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for record in records:
        score = _oracle_score(record)
        if score is not None:
            values.append(score)
    return values


def _uncertainty_score(record: Mapping[str, Any]) -> float | None:
    uncertainty = _mapping(_mapping(record.get("metadata")).get("uncertainty"))
    for key in ("overall_uncertainty", "score", "active_learning_value"):
        value = uncertainty.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    value = _mapping(record.get("score_breakdown")).get("uncertainty_score")
    return float(value) if isinstance(value, (int, float)) else None


def _uncertainty_scores(records: Sequence[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for record in records:
        score = _uncertainty_score(record)
        if score is not None:
            values.append(score)
    return values


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None}
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "mean": round(sum(ordered) / len(ordered), 3),
    }


def _generator_contribution(
    retained: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, int]] = {}
    for record in retained:
        generator = _generator(record)
        counts.setdefault(generator, {"generated_count": 0, "retained_count": 0})
        counts[generator]["generated_count"] += 1
        counts[generator]["retained_count"] += 1
    for record in rejected:
        generator = _generator(record)
        counts.setdefault(generator, {"generated_count": 0, "retained_count": 0})
        counts[generator]["generated_count"] += 1
    return {
        generator: {
            **values,
            "retention_rate": _rate(values["retained_count"], values["generated_count"]),
        }
        for generator, values in sorted(counts.items())
    }


def _generator(record: Mapping[str, Any]) -> str:
    return str(record.get("generation_method") or record.get("generator_name") or "unknown")


def _cost_per_retained(artifact: Mapping[str, Any], retained_count: int) -> float:
    metadata = _mapping(artifact.get("metadata"))
    cost = metadata.get("generation_cost")
    if not isinstance(cost, (int, float)):
        cost = artifact.get("generation_cost")
    if not isinstance(cost, (int, float)) or retained_count <= 0:
        return 0.0
    return round(max(0.0, float(cost)) / retained_count, 3)


def _active_learning_efficiency(
    artifact: Mapping[str, Any],
    retained: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    labels = artifact.get("experimental_results") or artifact.get("experimental_labels")
    if not isinstance(labels, list):
        return {"available": False, "labeled_count": 0, "sample_efficiency": None}
    retained_smiles = {_smiles(record) for record in retained if _smiles(record)}
    labeled = [label for label in labels if isinstance(label, Mapping)]
    exact = [
        label
        for label in labeled
        if str(label.get("canonical_smiles") or "") in retained_smiles
        or str(label.get("candidate_id") or "") in {_record_id(record) for record in retained}
    ]
    positives = [
        label
        for label in exact
        if str(label.get("outcome_label") or "").lower() == "positive"
        or str(label.get("activity_direction") or "").lower() in {"active", "improved"}
    ]
    return {
        "available": bool(exact),
        "labeled_count": len(exact),
        "positive_label_count": len(positives),
        "sample_efficiency": _rate(len(positives), len(exact)) if exact else None,
        "labels_are_exact_structure_only": True,
    }


def _record_id(record: Mapping[str, Any]) -> str:
    return str(record.get("generated_id") or record.get("candidate_id") or "")


def _guacamol_mode(config: Mapping[str, Any]) -> dict[str, Any]:
    enabled = bool(config.get("enable_guacamol", False))
    installed = importlib.util.find_spec("guacamol") is not None
    return {
        "enabled": enabled and installed,
        "requested": enabled,
        "dependency_installed": installed,
        "status": "available" if enabled and installed else "not_run",
        "reason": None
        if enabled and installed
        else "GuacaMol dependency is optional and not required by default.",
    }


def _pmo_mode(
    artifact: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    enabled = bool(config.get("enable_pmo_tracking", False) or config.get("pmo_oracle_budget"))
    metadata = _mapping(artifact.get("metadata"))
    calls = _nonnegative_int(
        config.get("pmo_oracle_calls") or metadata.get("oracle_call_count"),
        fallback=len(records),
    )
    budget = _nonnegative_int(config.get("pmo_oracle_budget"), fallback=0)
    return {
        "enabled": enabled,
        "oracle_calls_used": calls,
        "oracle_budget": budget,
        "budget_fraction_used": round(calls / budget, 3) if budget > 0 else None,
        "budget_exceeded": bool(budget > 0 and calls > budget),
    }


def _internal_target_conditioned_mode(
    records: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    enabled = bool(config.get("enable_internal_target_conditioned", True))
    target_counts: dict[str, int] = {}
    for record in records:
        targets = record.get("conditioned_targets")
        if isinstance(targets, list):
            for target in targets:
                if target not in (None, ""):
                    target_counts[str(target)] = target_counts.get(str(target), 0) + 1
    return {
        "enabled": enabled,
        "synthetic_objective": bool(config.get("use_synthetic_objective", False)),
        "target_conditioned_count": sum(target_counts.values()),
        "target_counts": dict(sorted(target_counts.items())),
    }


def _markdown_report(report: DesignBenchmarkReport) -> str:
    metrics = report.metrics
    lines = [
        "# Design Benchmark Report",
        "",
        f"- Benchmark: {report.benchmark_name}",
        f"- Random seed: {report.random_seed}",
        f"- Generated: {metrics.generated_count}",
        f"- Retained: {metrics.retained_count}",
        f"- Validity rate: {metrics.validity_rate:.3f}",
        f"- Uniqueness rate: {metrics.uniqueness_rate:.3f}",
        f"- Novelty rate: {metrics.novelty_rate:.3f}",
        f"- Diversity: {metrics.diversity:.3f}",
        f"- Scaffold diversity: {metrics.scaffold_diversity:.3f}",
        f"- Critical alert rate: {metrics.critical_alert_rate:.3f}",
        "",
        "Scores are computational benchmark signals only, not experimental evidence.",
    ]
    return "\n".join(lines) + "\n"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _nonnegative_int(value: Any, *, fallback: int) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return fallback


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(float(numerator) / float(denominator), 1.0)), 3)
