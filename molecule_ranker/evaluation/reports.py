from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from molecule_ranker.evaluation.schemas import (
    BenchmarkDataset,
    BenchmarkSplit,
    BenchmarkTask,
    DecisionQualityReport,
    EvaluationMetric,
    EvaluationReport,
    ReproducibilityManifest,
)

REQUIRED_REPORT_DISCLAIMERS = [
    "Benchmark results are evaluation artifacts.",
    "This is not clinical validation.",
    "This is not proof of efficacy, safety, activity, or synthesizability.",
    "No lab protocols are provided.",
    "No synthesis instructions are provided.",
    "No dosing or patient treatment guidance is provided.",
]


def write_evaluation_report(report: EvaluationReport, output_dir: str | Path) -> tuple[Path, Path]:
    resolved = Path(output_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    json_path = resolved / "evaluation_report.json"
    markdown_path = resolved / "evaluation_report.md"
    payload = report.model_dump(mode="json")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_evaluation_report_markdown(report))
    return json_path, markdown_path


def write_benchmark_suite_report(
    report: EvaluationReport,
    output_dir: str | Path,
    *,
    task: BenchmarkTask | Mapping[str, Any] | None = None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None = None,
    split: BenchmarkSplit | Mapping[str, Any] | None = None,
    guardrail_results: Mapping[str, Any] | None = None,
) -> Path:
    return _write_named_report(
        output_dir,
        "benchmark_suite_report.md",
        render_benchmark_suite_report(
            report,
            task=task,
            dataset=dataset,
            split=split,
            guardrail_results=guardrail_results,
        ),
    )


def write_prospective_validation_report(
    report: EvaluationReport,
    output_dir: str | Path,
    *,
    task: BenchmarkTask | Mapping[str, Any] | None = None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None = None,
    split: BenchmarkSplit | Mapping[str, Any] | None = None,
    guardrail_results: Mapping[str, Any] | None = None,
) -> Path:
    return _write_named_report(
        output_dir,
        "prospective_validation_report.md",
        render_prospective_validation_report(
            report,
            task=task,
            dataset=dataset,
            split=split,
            guardrail_results=guardrail_results,
        ),
    )


def write_decision_quality_report(
    report: DecisionQualityReport,
    output_dir: str | Path,
    *,
    guardrail_results: Mapping[str, Any] | None = None,
) -> Path:
    return _write_named_report(
        output_dir,
        "decision_quality_report.md",
        render_decision_quality_report(report, guardrail_results=guardrail_results),
    )


def write_guardrail_benchmark_report(
    report: EvaluationReport,
    output_dir: str | Path,
    *,
    task: BenchmarkTask | Mapping[str, Any] | None = None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None = None,
    split: BenchmarkSplit | Mapping[str, Any] | None = None,
) -> Path:
    return _write_named_report(
        output_dir,
        "guardrail_benchmark_report.md",
        render_guardrail_benchmark_report(report, task=task, dataset=dataset, split=split),
    )


def write_reproducibility_report(
    manifest: ReproducibilityManifest,
    output_dir: str | Path,
    *,
    report: Mapping[str, Any] | None = None,
) -> Path:
    return _write_named_report(
        output_dir,
        "reproducibility_report.md",
        render_reproducibility_report(manifest, report=report),
    )


def write_longitudinal_trend_report(
    trend: Mapping[str, Any],
    output_dir: str | Path,
) -> Path:
    return _write_named_report(
        output_dir,
        "longitudinal_trend_report.md",
        render_longitudinal_trend_report(trend),
    )


def render_evaluation_report_markdown(report: EvaluationReport) -> str:
    return render_benchmark_suite_report(report)


def render_benchmark_suite_report(
    report: EvaluationReport,
    *,
    task: BenchmarkTask | Mapping[str, Any] | None = None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None = None,
    split: BenchmarkSplit | Mapping[str, Any] | None = None,
    guardrail_results: Mapping[str, Any] | None = None,
) -> str:
    return _render_evaluation_report(
        title="Benchmark Suite Report",
        report=report,
        task=task,
        dataset=dataset,
        split=split,
        guardrail_results=guardrail_results,
    )


def render_prospective_validation_report(
    report: EvaluationReport,
    *,
    task: BenchmarkTask | Mapping[str, Any] | None = None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None = None,
    split: BenchmarkSplit | Mapping[str, Any] | None = None,
    guardrail_results: Mapping[str, Any] | None = None,
) -> str:
    return _render_evaluation_report(
        title="Prospective Validation Report",
        report=report,
        task=task,
        dataset=dataset,
        split=split,
        guardrail_results=guardrail_results,
        interpretation_note=(
            "Prospective analytics describe performance against later imported outcomes; "
            "they do not validate clinical utility."
        ),
    )


def render_guardrail_benchmark_report(
    report: EvaluationReport,
    *,
    task: BenchmarkTask | Mapping[str, Any] | None = None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None = None,
    split: BenchmarkSplit | Mapping[str, Any] | None = None,
) -> str:
    return _render_evaluation_report(
        title="Guardrail Benchmark Report",
        report=report,
        task=task,
        dataset=dataset,
        split=split,
        guardrail_results=_metadata_mapping(report, "guardrail_results"),
        interpretation_note="Guardrail failures must be reviewed and surfaced, not hidden.",
    )


def render_decision_quality_report(
    report: DecisionQualityReport,
    *,
    guardrail_results: Mapping[str, Any] | None = None,
) -> str:
    metrics = [_metric_to_mapping(metric) for metric in report.metrics]
    lines = _base_header("Decision Quality Report")
    lines.extend(
        [
            "## Task Definition",
            "",
            _json_block(
                {
                    "report_id": report.report_id,
                    "project_id": report.project_id,
                    "campaign_id": report.campaign_id,
                }
            ),
            "",
            "## Dataset And Source Provenance",
            "",
            _json_block(
                {
                    "decision_artifact_ids": report.decision_artifact_ids,
                    "outcome_artifact_ids": report.outcome_artifact_ids,
                }
            ),
            "",
            "## Splits",
            "",
            "- Not applicable to this decision quality report unless supplied in metadata.",
            "",
            "## Baselines",
            "",
            _json_block(report.metadata.get("baselines", [])),
            "",
            "## Metrics",
            "",
            *_metric_lines(metrics),
            "",
            "## Confidence And Uncertainty",
            "",
            *_uncertainty_lines(metrics),
            "",
            "## Limitations",
            "",
            *_limitation_lines(report.warnings),
            "",
            "## Guardrail Results",
            "",
            _json_block(guardrail_results or report.metadata.get("guardrail_results", {})),
            "",
            "## Interpretation Guidance",
            "",
            "- Decision quality reports describe process alignment, not molecule proof.",
            "- Lessons should be interpreted within the imported evidence context.",
            "",
            "## Decision Summary",
            "",
            _json_block(report.decision_summary),
            "",
            "## Lessons",
            "",
            *[f"- {lesson}" for lesson in report.lessons],
            "",
        ]
    )
    return "\n".join(lines)


def render_reproducibility_report(
    manifest: ReproducibilityManifest,
    *,
    report: Mapping[str, Any] | None = None,
) -> str:
    payload = report or {}
    lines = _base_header("Reproducibility Report")
    lines.extend(
        [
            "## Task Definition",
            "",
            _json_block(
                {
                    "manifest_id": manifest.manifest_id,
                    "run_id": manifest.run_id,
                    "suite_id": manifest.suite_id,
                    "code_version": manifest.code_version,
                    "artifact_contract_version": manifest.artifact_contract_version,
                }
            ),
            "",
            "## Dataset And Source Provenance",
            "",
            _json_block(
                {
                    "input_artifact_hashes": manifest.input_artifact_hashes,
                    "output_artifact_hashes": manifest.output_artifact_hashes,
                }
            ),
            "",
            "## Splits",
            "",
            _json_block(manifest.metadata.get("split", {})),
            "",
            "## Baselines",
            "",
            _json_block(manifest.metadata.get("baselines", [])),
            "",
            "## Metrics",
            "",
            _json_block(payload.get("metrics", {})),
            "",
            "## Confidence And Uncertainty",
            "",
            "- Hash agreement is exact for local artifact bytes.",
            "- Deterministic rerun checks are limited to the compared artifacts.",
            "",
            "## Limitations",
            "",
            *_limitation_lines(payload.get("warnings", [])),
            "",
            "## Guardrail Results",
            "",
            _json_block(manifest.metadata.get("guardrail_results", {})),
            "",
            "## Interpretation Guidance",
            "",
            "- Reproducibility reports verify consistency of recorded artifacts and settings.",
            "- Reproducibility does not establish scientific truth or clinical utility.",
            "",
        ]
    )
    return "\n".join(lines)


def render_longitudinal_trend_report(trend: Mapping[str, Any]) -> str:
    lines = _base_header("Longitudinal Trend Report")
    lines.extend(
        [
            "## Task Definition",
            "",
            _json_block(trend.get("task_definition", {"trend_id": trend.get("trend_id")})),
            "",
            "## Dataset And Source Provenance",
            "",
            _json_block(trend.get("dataset_provenance", {})),
            "",
            "## Splits",
            "",
            _json_block(trend.get("splits", [])),
            "",
            "## Baselines",
            "",
            _json_block(trend.get("baselines", [])),
            "",
            "## Metrics",
            "",
            _json_block(trend.get("metrics", [])),
            "",
            "## Confidence And Uncertainty",
            "",
            _json_block(trend.get("uncertainty", {})),
            "",
            "## Limitations",
            "",
            *_limitation_lines(trend.get("limitations", [])),
            "",
            "## Guardrail Results",
            "",
            _json_block(trend.get("guardrail_results", {})),
            "",
            "## Interpretation Guidance",
            "",
            "- Longitudinal trends can indicate benchmark movement, not molecule proof.",
            "- Compare trends only across compatible datasets, splits, and frozen artifacts.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_evaluation_report(
    *,
    title: str,
    report: EvaluationReport,
    task: BenchmarkTask | Mapping[str, Any] | None,
    dataset: BenchmarkDataset | Mapping[str, Any] | None,
    split: BenchmarkSplit | Mapping[str, Any] | None,
    guardrail_results: Mapping[str, Any] | None,
    interpretation_note: str | None = None,
) -> str:
    task_payload = _task_payload(report, task)
    dataset_payload = _dataset_payload(report, dataset)
    split_payload = _split_payload(report, split)
    metrics = [_metric_to_mapping(metric) for metric in report.metrics]
    baselines = [_metric_to_mapping(metric) for metric in report.baseline_metrics]
    lines = _base_header(title)
    lines.extend(
        [
            "## Task Definition",
            "",
            _json_block(task_payload),
            "",
            "## Dataset And Source Provenance",
            "",
            _json_block(dataset_payload),
            "",
            "## Splits",
            "",
            _json_block(split_payload),
            "",
            "## Baselines",
            "",
            *_baseline_lines(baselines, report.comparisons),
            "",
            "## Metrics",
            "",
            *_metric_lines(metrics),
            "",
            "## Confidence And Uncertainty",
            "",
            *_uncertainty_lines(metrics),
            "",
            "## Limitations",
            "",
            *_limitation_lines(report.limitations),
            "",
            "## Guardrail Results",
            "",
            _json_block(guardrail_results or _metadata_mapping(report, "guardrail_results")),
            "",
            "## Interpretation Guidance",
            "",
            "- Treat this report as an evaluation artifact for comparing benchmark behavior.",
            "- Use imported labels, frozen predictions, and provenance to interpret results.",
            "- Do not treat benchmark metrics as claims about real-world molecule performance.",
        ]
    )
    if interpretation_note:
        lines.append(f"- {interpretation_note}")
    lines.append("")
    if report.warnings:
        lines.extend(["## Warnings", "", *[f"- {warning}" for warning in report.warnings], ""])
    return "\n".join(lines)


def _base_header(title: str) -> list[str]:
    return [
        f"# {title}",
        "",
        "## Required Disclaimers",
        "",
        *[f"- {disclaimer}" for disclaimer in REQUIRED_REPORT_DISCLAIMERS],
        "",
    ]


def _task_payload(
    report: EvaluationReport,
    task: BenchmarkTask | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if task is not None:
        return _model_or_mapping(task)
    metadata_task = report.metadata.get("task_definition")
    if isinstance(metadata_task, Mapping):
        return dict(metadata_task)
    return {
        "evaluation_id": report.evaluation_id,
        "suite_id": report.suite_id,
        "task_id": report.task_id,
        "prediction_set_id": report.prediction_set_id,
    }


def _dataset_payload(
    report: EvaluationReport,
    dataset: BenchmarkDataset | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if dataset is not None:
        return _model_or_mapping(dataset)
    metadata_dataset = report.metadata.get("dataset_provenance")
    if isinstance(metadata_dataset, Mapping):
        return dict(metadata_dataset)
    return {"dataset_id": report.dataset_id}


def _split_payload(
    report: EvaluationReport,
    split: BenchmarkSplit | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if split is not None:
        return _model_or_mapping(split)
    metadata_split = report.metadata.get("split")
    if isinstance(metadata_split, Mapping):
        return dict(metadata_split)
    return {"split_id": report.split_id}


def _model_or_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


def _metric_to_mapping(metric: EvaluationMetric) -> dict[str, Any]:
    return metric.model_dump(mode="json")


def _metric_lines(metrics: Sequence[Mapping[str, Any]]) -> list[str]:
    if not metrics:
        return ["- none"]
    return [_metric_line(metric) for metric in metrics]


def _metric_line(metric: Mapping[str, Any]) -> str:
    direction = metric.get("higher_is_better")
    if direction is True:
        direction_label = "higher is better"
    elif direction is False:
        direction_label = "lower is better"
    else:
        direction_label = "direction not specified"
    ci = metric.get("confidence_interval")
    uncertainty = f", CI={ci}" if ci else ""
    return f"- `{metric.get('name')}`: {metric.get('value')} ({direction_label}{uncertainty})"


def _baseline_lines(
    baseline_metrics: Sequence[Mapping[str, Any]],
    comparisons: Sequence[Mapping[str, Any]],
) -> list[str]:
    lines: list[str] = []
    if baseline_metrics:
        lines.extend(_metric_lines(baseline_metrics))
    else:
        lines.append("- No baseline metrics supplied.")
    if comparisons:
        lines.append("")
        lines.append("Comparisons:")
        lines.extend(f"- {_json_inline(comparison)}" for comparison in comparisons)
    return lines


def _uncertainty_lines(metrics: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for metric in metrics:
        ci = metric.get("confidence_interval")
        metadata = metric.get("metadata")
        if ci:
            lines.append(f"- `{metric.get('name')}` confidence interval: {_json_inline(ci)}")
        if isinstance(metadata, Mapping) and metadata.get("uncertainty"):
            lines.append(f"- `{metric.get('name')}` uncertainty: {metadata['uncertainty']}")
    if not lines:
        lines.append("- No confidence interval or uncertainty estimate supplied.")
    return lines


def _limitation_lines(limitations: Any) -> list[str]:
    lines = [f"- {disclaimer}" for disclaimer in REQUIRED_REPORT_DISCLAIMERS]
    if isinstance(limitations, str):
        extra = [limitations]
    elif isinstance(limitations, Sequence):
        extra = [str(item) for item in limitations]
    else:
        extra = []
    for limitation in extra:
        if limitation not in REQUIRED_REPORT_DISCLAIMERS:
            lines.append(f"- {limitation}")
    return lines


def _metadata_mapping(report: EvaluationReport, key: str) -> dict[str, Any]:
    value = report.metadata.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True, default=str) + "\n```"


def _json_inline(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _write_named_report(output_dir: str | Path, filename: str, markdown: str) -> Path:
    resolved = Path(output_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    path = resolved / filename
    path.write_text(markdown)
    return path


__all__ = [
    "REQUIRED_REPORT_DISCLAIMERS",
    "render_benchmark_suite_report",
    "render_decision_quality_report",
    "render_evaluation_report_markdown",
    "render_guardrail_benchmark_report",
    "render_longitudinal_trend_report",
    "render_prospective_validation_report",
    "render_reproducibility_report",
    "write_benchmark_suite_report",
    "write_decision_quality_report",
    "write_evaluation_report",
    "write_guardrail_benchmark_report",
    "write_longitudinal_trend_report",
    "write_prospective_validation_report",
    "write_reproducibility_report",
]
