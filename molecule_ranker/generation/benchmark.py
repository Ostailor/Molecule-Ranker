from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity


class GenerationBenchmarkError(ValueError):
    """Raised when a generated-molecule benchmark artifact cannot be parsed."""


class GenerationBenchmarkAdapter(Protocol):
    """Adapter shape for future GuacaMol-style benchmark integrations."""

    name: str

    def benchmark(self, artifact: Mapping[str, Any]) -> GenerationBenchmarkResult:
        """Return benchmark metrics for a generated molecule artifact."""
        ...


class DescriptorSummary(BaseModel):
    min: float
    max: float
    mean: float


class GenerationBenchmarkResult(BaseModel):
    validity_rate: float = Field(ge=0.0, le=1.0)
    uniqueness_rate: float = Field(ge=0.0, le=1.0)
    novelty_rate: float = Field(ge=0.0, le=1.0)
    near_duplicate_rate: float = Field(ge=0.0, le=1.0)
    retained_rate: float = Field(ge=0.0, le=1.0)
    average_similarity_to_seed: float
    average_similarity_to_existing: float
    descriptor_distribution_summary: dict[str, DescriptorSummary] = Field(
        default_factory=dict
    )
    target_coverage: dict[str, int] = Field(default_factory=dict)
    diversity_cluster_count: int = 0
    average_experiment_readiness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    average_uncertainty_score: float = Field(default=0.0, ge=0.0, le=1.0)
    review_ready_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    generator_method_counts: dict[str, int] = Field(default_factory=dict)
    generated_count: int = 0
    retained_count: int = 0
    rejected_count: int = 0


class InternalGenerationBenchmark:
    """Internal distribution-style benchmark for generated molecule artifacts."""

    name = "internal_generation_quality_v1_1"

    def benchmark(self, artifact: Mapping[str, Any]) -> GenerationBenchmarkResult:
        records = _records_from_artifact(artifact)
        generated_count = _positive_int(
            artifact.get("generated_count"),
            fallback=len(records),
        )
        retained_records = _retained_records_from_artifact(artifact)
        rejected_records = _rejected_records_from_artifact(artifact)
        retained_count = _positive_int(
            artifact.get("retained_count"),
            fallback=len(retained_records),
        )
        rejected_count = _positive_int(
            artifact.get("rejected_count"),
            fallback=len(rejected_records),
        )

        if generated_count == 0:
            return GenerationBenchmarkResult(
                validity_rate=0.0,
                uniqueness_rate=0.0,
                novelty_rate=0.0,
                near_duplicate_rate=0.0,
                retained_rate=0.0,
                average_similarity_to_seed=0.0,
                average_similarity_to_existing=0.0,
                descriptor_distribution_summary={},
                target_coverage={},
                diversity_cluster_count=0,
                average_experiment_readiness_score=0.0,
                average_uncertainty_score=0.0,
                review_ready_rate=0.0,
                generator_method_counts={},
                generated_count=0,
                retained_count=0,
                rejected_count=0,
            )

        valid_records = [record for record in records if _valid_smiles(record)]
        retained_smiles = [
            smiles
            for record in retained_records
            if (smiles := _canonical_smiles(record))
        ]
        uniqueness_rate = _rate(len(set(retained_smiles)), len(retained_smiles))
        novelty_classes = [_novelty_class(record) for record in records]
        novelty_rate = _rate(
            sum(
                1
                for novelty_class in novelty_classes
                if novelty_class in {"close_analog", "novel_analog", "distant"}
            ),
            len(novelty_classes),
        )
        near_duplicate_rate = _rate(
            sum(
                1
                for novelty_class in novelty_classes
                if novelty_class in {"duplicate", "near_duplicate"}
            ),
            len(novelty_classes),
        )

        return GenerationBenchmarkResult(
            validity_rate=_rate(len(valid_records), generated_count),
            uniqueness_rate=uniqueness_rate,
            novelty_rate=novelty_rate,
            near_duplicate_rate=near_duplicate_rate,
            retained_rate=_rate(retained_count, generated_count),
            average_similarity_to_seed=_average_similarity(
                records,
                "max_similarity_to_seed",
            ),
            average_similarity_to_existing=_average_similarity(
                records,
                "max_similarity_to_existing",
            ),
            descriptor_distribution_summary=_descriptor_summary(retained_records),
            target_coverage=_target_coverage(retained_records),
            diversity_cluster_count=len(
                {
                    str(record["diversity_cluster"])
                    for record in retained_records
                    if record.get("diversity_cluster")
                }
            ),
            average_experiment_readiness_score=_metadata_score_average(
                retained_records,
                "experiment_readiness",
            ),
            average_uncertainty_score=_metadata_score_average(
                retained_records,
                "uncertainty",
            ),
            review_ready_rate=_review_ready_rate(retained_records),
            generator_method_counts=_generator_method_counts(records),
            generated_count=generated_count,
            retained_count=retained_count,
            rejected_count=rejected_count,
        )


def benchmark_generated_file(path: Path) -> GenerationBenchmarkResult:
    artifact = load_generated_artifact(path)
    return InternalGenerationBenchmark().benchmark(artifact)


def load_generated_artifact(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text()
    except OSError as exc:
        raise GenerationBenchmarkError(f"Could not read generated artifact: {path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GenerationBenchmarkError("Generated artifact is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise GenerationBenchmarkError("Generated artifact must be a JSON object.")
    _records_from_artifact(payload)
    return payload


def _records_from_artifact(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    retained = _retained_records_from_artifact(artifact)
    rejected = _rejected_records_from_artifact(artifact)
    if retained or rejected:
        return [*retained, *rejected]
    if _positive_int(artifact.get("generated_count"), fallback=0) == 0:
        return []
    raise GenerationBenchmarkError(
        "Generated artifact does not contain retained or rejected generated molecules."
    )


def _retained_records_from_artifact(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    retained = artifact.get("retained_generated_molecules")
    if isinstance(retained, list):
        return [_expect_mapping(item, "retained_generated_molecules") for item in retained]
    legacy = artifact.get("generated_molecule_hypotheses")
    if isinstance(legacy, list):
        return [_legacy_record(item) for item in legacy]
    return []


def _rejected_records_from_artifact(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    rejected = artifact.get("rejected_generated_molecules")
    if not isinstance(rejected, list):
        return []
    records: list[dict[str, Any]] = []
    for item in rejected:
        wrapper = _expect_mapping(item, "rejected_generated_molecules")
        generated = wrapper.get("generated_molecule", wrapper)
        record = _expect_mapping(generated, "rejected generated_molecule")
        reasons = wrapper.get("rejection_reasons")
        if isinstance(reasons, list):
            record = {**record, "benchmark_rejection_reasons": list(reasons)}
        records.append(record)
    return records


def _legacy_record(item: Any) -> dict[str, Any]:
    record = _expect_mapping(item, "generated_molecule_hypotheses")
    target_symbol = record.get("target_symbol")
    return {
        "generated_id": record.get("name"),
        "canonical_smiles": record.get("canonical_smiles"),
        "descriptors": record.get("descriptors", {}),
        "conditioned_targets": [target_symbol] if target_symbol else [],
        "generation_score": record.get("generation_score"),
        "novelty": {
            "max_similarity_to_seed": record.get("max_seed_similarity", 0.0),
            "max_similarity_to_existing": 0.0,
            "novelty_class": "novel_analog",
        },
        "diversity_cluster": record.get("trace", {}).get("diversity_cluster"),
    }


def _expect_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GenerationBenchmarkError(f"{field_name} entries must be JSON objects.")
    return dict(value)


def _valid_smiles(record: Mapping[str, Any]) -> bool:
    validation = record.get("validation")
    if isinstance(validation, dict) and validation.get("valid_rdkit_mol") is False:
        return False
    smiles = _canonical_smiles(record)
    return bool(smiles and mol_from_smiles(smiles) is not None)


def _canonical_smiles(record: Mapping[str, Any]) -> str | None:
    value = record.get("canonical_smiles") or record.get("smiles")
    return str(value) if value not in (None, "") else None


def _novelty_class(record: Mapping[str, Any]) -> str | None:
    novelty = record.get("novelty")
    if isinstance(novelty, dict):
        value = novelty.get("novelty_class")
        return str(value) if value not in (None, "") else None
    return None


def _average_similarity(records: Sequence[Mapping[str, Any]], key: str) -> float:
    values: list[float] = []
    for record in records:
        novelty = record.get("novelty")
        if isinstance(novelty, dict) and isinstance(novelty.get(key), (int, float)):
            values.append(float(novelty[key]))
            continue
        if key == "max_similarity_to_seed" and isinstance(
            record.get("max_seed_similarity"),
            (int, float),
        ):
            values.append(float(record["max_seed_similarity"]))
            continue
        computed = _computed_average_similarity(record, key)
        if computed is not None:
            values.append(computed)
    return round(sum(values) / len(values), 3) if values else 0.0


def _computed_average_similarity(record: Mapping[str, Any], key: str) -> float | None:
    smiles = _canonical_smiles(record)
    if smiles is None:
        return None
    mol = mol_from_smiles(smiles)
    if mol is None:
        return None
    reference_smiles = []
    if key == "max_similarity_to_seed":
        reference_smiles = _reference_smiles(record, "source_seed_smiles")
    elif key == "max_similarity_to_existing":
        reference_smiles = _reference_smiles(record, "nearest_existing_smiles")
    similarities = [
        tanimoto_similarity(mol, reference_mol)
        for ref in reference_smiles
        if (reference_mol := mol_from_smiles(ref)) is not None
    ]
    return max(similarities) if similarities else None


def _reference_smiles(record: Mapping[str, Any], key: str) -> list[str]:
    metadata = record.get("metadata")
    values = metadata.get(key) if isinstance(metadata, dict) else None
    if isinstance(values, list):
        return [str(value) for value in values if value not in (None, "")]
    if isinstance(values, str):
        return [values]
    return []


def _descriptor_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, DescriptorSummary]:
    values_by_descriptor: dict[str, list[float]] = {}
    for record in records:
        descriptors = record.get("descriptors")
        if not isinstance(descriptors, dict):
            continue
        for name, value in descriptors.items():
            if isinstance(value, (int, float)):
                values_by_descriptor.setdefault(str(name), []).append(float(value))
    return {
        name: DescriptorSummary(
            min=round(min(values), 3),
            max=round(max(values), 3),
            mean=round(sum(values) / len(values), 3),
        )
        for name, values in sorted(values_by_descriptor.items())
        if values
    }


def _target_coverage(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    coverage: dict[str, int] = {}
    for record in records:
        targets = record.get("conditioned_targets")
        if isinstance(targets, list):
            for target in targets:
                if target not in (None, ""):
                    coverage[str(target)] = coverage.get(str(target), 0) + 1
        else:
            target = record.get("target_symbol")
            if target not in (None, ""):
                coverage[str(target)] = coverage.get(str(target), 0) + 1
    return dict(sorted(coverage.items()))


def _metadata_score_average(records: Sequence[Mapping[str, Any]], key: str) -> float:
    values: list[float] = []
    for record in records:
        metadata = record.get("metadata")
        nested = metadata.get(key) if isinstance(metadata, dict) else None
        if isinstance(nested, dict) and isinstance(nested.get("score"), (int, float)):
            values.append(float(nested["score"]))
            continue
        score_breakdown = record.get("score_breakdown")
        if not isinstance(score_breakdown, dict):
            continue
        field = (
            "experiment_readiness_score"
            if key == "experiment_readiness"
            else "uncertainty_score"
        )
        if isinstance(score_breakdown.get(field), (int, float)):
            values.append(float(score_breakdown[field]))
    return round(sum(values) / len(values), 3) if values else 0.0


def _review_ready_rate(records: Sequence[Mapping[str, Any]]) -> float:
    labels: list[str] = []
    for record in records:
        metadata = record.get("metadata")
        readiness = metadata.get("experiment_readiness") if isinstance(metadata, dict) else None
        if isinstance(readiness, dict) and readiness.get("label"):
            labels.append(str(readiness["label"]))
    return _rate(sum(1 for label in labels if label == "review_ready"), len(labels))


def _generator_method_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        method = record.get("generation_method") or record.get("source") or "unknown"
        method = str(method)
        counts[method] = counts.get(method, 0) + 1
    return dict(sorted(counts.items()))


def _positive_int(value: Any, *, fallback: int) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return fallback


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(float(numerator) / float(denominator), 1.0)), 3)
