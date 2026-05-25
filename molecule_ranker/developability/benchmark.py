from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DevelopabilityBenchmarkError(ValueError):
    """Raised when a developability benchmark artifact cannot be parsed."""


class DevelopabilityScoreDistribution(BaseModel):
    min: float
    max: float
    mean: float
    buckets: dict[str, int] = Field(default_factory=dict)


class DevelopabilityBenchmarkResult(BaseModel):
    descriptor_coverage: float = Field(ge=0.0, le=1.0)
    alert_rate: float = Field(ge=0.0, le=1.0)
    critical_alert_rate: float = Field(ge=0.0, le=1.0)
    high_risk_admet_rate: float = Field(ge=0.0, le=1.0)
    synthesized_complexity_distribution: dict[str, int] = Field(default_factory=dict)
    generated_retention_rate_after_developability: float = Field(ge=0.0, le=1.0)
    developability_score_distribution: DevelopabilityScoreDistribution
    risk_level_distribution: dict[str, int] = Field(default_factory=dict)
    endpoint_coverage: dict[str, int] = Field(default_factory=dict)
    assessment_count: int = 0
    assessed_existing_count: int = 0
    assessed_generated_count: int = 0
    retained_count: int = 0
    deprioritized_count: int = 0
    rejected_count: int = 0
    tdc_benchmark_enabled: bool = False
    tdc_benchmark_available: bool = False
    tdc_benchmark_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class InternalDevelopabilityBenchmark:
    """Internal distribution benchmark for V0.4 developability artifacts."""

    name = "internal_developability_calibration_v0_4"

    def benchmark(
        self,
        artifact: Mapping[str, Any],
        *,
        enable_tdc_benchmark: bool = False,
        tdc_data_dir: Path | None = None,
    ) -> DevelopabilityBenchmarkResult:
        assessments = _assessments_from_artifact(artifact)
        total = len(assessments)
        generated = [
            assessment for assessment in assessments if assessment.get("origin") == "generated"
        ]
        warnings = list(_string_list(artifact.get("warnings")))
        tdc_available = _tdc_available()
        if enable_tdc_benchmark and not tdc_available:
            warnings.append(
                "TDC benchmark mode was requested, but the optional tdc package is not installed."
            )

        return DevelopabilityBenchmarkResult(
            descriptor_coverage=_rate(
                sum(1 for assessment in assessments if _has_physchem(assessment)),
                total,
            ),
            alert_rate=_rate(
                sum(1 for assessment in assessments if _alerts(assessment)),
                total,
            ),
            critical_alert_rate=_rate(
                sum(
                    1
                    for assessment in assessments
                    if any(alert.get("severity") == "critical" for alert in _alerts(assessment))
                ),
                total,
            ),
            high_risk_admet_rate=_rate(
                sum(
                    1
                    for assessment in assessments
                    if any(
                        prediction.get("risk_level") == "high"
                        for prediction in _admet_predictions(assessment)
                    )
                ),
                total,
            ),
            synthesized_complexity_distribution=_complexity_distribution(assessments),
            generated_retention_rate_after_developability=_generated_retention_rate(
                generated,
                artifact,
            ),
            developability_score_distribution=_score_distribution(assessments),
            risk_level_distribution=_risk_distribution(assessments),
            endpoint_coverage=_endpoint_coverage(assessments),
            assessment_count=total,
            assessed_existing_count=_artifact_count(
                artifact,
                "assessed_existing_count",
                fallback=sum(
                    1 for assessment in assessments if assessment.get("origin") == "existing"
                ),
            ),
            assessed_generated_count=_artifact_count(
                artifact,
                "assessed_generated_count",
                fallback=len(generated),
            ),
            retained_count=_artifact_count(artifact, "retained_count", fallback=0),
            deprioritized_count=_artifact_count(artifact, "deprioritized_count", fallback=0),
            rejected_count=_artifact_count(artifact, "rejected_count", fallback=0),
            tdc_benchmark_enabled=enable_tdc_benchmark,
            tdc_benchmark_available=tdc_available,
            tdc_benchmark_summary=_tdc_summary(
                enabled=enable_tdc_benchmark,
                available=tdc_available,
                tdc_data_dir=tdc_data_dir,
            ),
            warnings=warnings,
        )


def benchmark_developability_file(
    path: Path,
    *,
    enable_tdc_benchmark: bool = False,
    tdc_data_dir: Path | None = None,
) -> DevelopabilityBenchmarkResult:
    artifact = load_developability_artifact(path)
    return InternalDevelopabilityBenchmark().benchmark(
        artifact,
        enable_tdc_benchmark=enable_tdc_benchmark,
        tdc_data_dir=tdc_data_dir,
    )


def load_developability_artifact(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        raise DevelopabilityBenchmarkError(
            f"Could not read developability artifact: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DevelopabilityBenchmarkError("Developability artifact is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise DevelopabilityBenchmarkError("Developability artifact must be a JSON object.")
    _assessments_from_artifact(payload)
    return payload


def _assessments_from_artifact(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    assessments = artifact.get("assessments")
    if assessments is None:
        run = artifact.get("developability_run")
        assessments = run.get("assessments") if isinstance(run, dict) else None
    if assessments is None:
        return []
    if not isinstance(assessments, list):
        raise DevelopabilityBenchmarkError("developability assessments must be a list.")
    return [_expect_mapping(item, "assessments") for item in assessments]


def _expect_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DevelopabilityBenchmarkError(f"{field_name} entries must be JSON objects.")
    return dict(value)


def _has_physchem(assessment: Mapping[str, Any]) -> bool:
    physchem = assessment.get("physchem")
    if not isinstance(physchem, dict):
        return False
    descriptor_fields = (
        "molecular_weight",
        "logp",
        "tpsa",
        "hbd",
        "hba",
        "rotatable_bonds",
    )
    return any(physchem.get(field) is not None for field in descriptor_fields)


def _alerts(assessment: Mapping[str, Any]) -> list[dict[str, Any]]:
    alerts = assessment.get("alerts")
    if not isinstance(alerts, list):
        return []
    return [alert for alert in alerts if isinstance(alert, dict)]


def _admet_predictions(assessment: Mapping[str, Any]) -> list[dict[str, Any]]:
    predictions = assessment.get("admet_predictions")
    if not isinstance(predictions, list):
        return []
    return [prediction for prediction in predictions if isinstance(prediction, dict)]


def _complexity_distribution(assessments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for assessment in assessments:
        synthesizability = assessment.get("synthesizability")
        if not isinstance(synthesizability, dict):
            complexity = "unknown"
        else:
            complexity = str(synthesizability.get("estimated_complexity") or "unknown")
        distribution[complexity] = distribution.get(complexity, 0) + 1
    return dict(sorted(distribution.items()))


def _generated_retention_rate(
    generated: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
) -> float:
    assessed_generated = _artifact_count(
        artifact,
        "assessed_generated_count",
        fallback=len(generated),
    )
    if assessed_generated <= 0:
        return 0.0
    generated_rejects = sum(
        1 for assessment in generated if assessment.get("recommendation") == "reject"
    )
    rejected_count = min(
        assessed_generated,
        max(generated_rejects, _artifact_count(artifact, "rejected_count", fallback=0)),
    )
    return _rate(assessed_generated - rejected_count, assessed_generated)


def _score_distribution(
    assessments: Sequence[Mapping[str, Any]],
) -> DevelopabilityScoreDistribution:
    values = [
        float(score)
        for assessment in assessments
        if isinstance(
            (score := assessment.get("overall_developability_score")),
            (int, float),
        )
    ]
    if not values:
        return DevelopabilityScoreDistribution(
            min=0.0,
            max=0.0,
            mean=0.0,
            buckets={"0.00-0.35": 0, "0.35-0.55": 0, "0.55-0.75": 0, "0.75-1.00": 0},
        )
    buckets = {"0.00-0.35": 0, "0.35-0.55": 0, "0.55-0.75": 0, "0.75-1.00": 0}
    for value in values:
        if value < 0.35:
            buckets["0.00-0.35"] += 1
        elif value < 0.55:
            buckets["0.35-0.55"] += 1
        elif value < 0.75:
            buckets["0.55-0.75"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return DevelopabilityScoreDistribution(
        min=round(min(values), 3),
        max=round(max(values), 3),
        mean=round(sum(values) / len(values), 3),
        buckets=buckets,
    )


def _risk_distribution(assessments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for assessment in assessments:
        risk = str(assessment.get("risk_level") or "unknown")
        distribution[risk] = distribution.get(risk, 0) + 1
    return dict(sorted(distribution.items()))


def _endpoint_coverage(assessments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    coverage: dict[str, int] = {}
    for assessment in assessments:
        for prediction in _admet_predictions(assessment):
            endpoint = str(prediction.get("endpoint") or "unknown")
            coverage[endpoint] = coverage.get(endpoint, 0) + 1
    return dict(sorted(coverage.items()))


def _artifact_count(artifact: Mapping[str, Any], key: str, *, fallback: int) -> int:
    value = artifact.get(key)
    if isinstance(value, int) and value >= 0:
        return value
    return fallback


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(float(numerator) / float(denominator), 1.0)), 3)


def _tdc_available() -> bool:
    return importlib.util.find_spec("tdc") is not None


def _tdc_summary(
    *,
    enabled: bool,
    available: bool,
    tdc_data_dir: Path | None,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "message": "TDC benchmark mode was not requested."}
    if not available:
        return {
            "enabled": True,
            "available": False,
            "message": "Install the optional tdc package to run external ADMET benchmarks.",
        }
    return {
        "enabled": True,
        "available": True,
        "data_dir": str(tdc_data_dir) if tdc_data_dir is not None else None,
        "message": (
            "TDC is available. Dataset-specific local ADMET model evaluation is reserved "
            "for explicitly configured model adapters."
        ),
    }
