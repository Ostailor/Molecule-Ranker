from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.evaluation.reports import render_prospective_validation_report
from molecule_ranker.evaluation.schemas import (
    EvaluationMetric,
    EvaluationReport,
    FrozenPredictionSet,
    ProspectiveValidationRun,
)

ArtifactLike = str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]]

RUN_PATH = "prospective_run.json"
FROZEN_SET_PATH = "frozen_prediction_set.json"
FROZEN_PAYLOAD_PATH = "prediction_artifact_frozen.json"
OUTCOME_PAYLOAD_PATH = "outcome_artifact.json"
HASHES_PATH = "artifact_hashes.json"
TASK_PATH = "prospective_task.json"
REPORT_JSON_PATH = "prospective_validation_report.json"
REPORT_MARKDOWN_PATH = "prospective_validation_report.md"


def create_prospective_task(
    *,
    task_id: str,
    output_dir: str | Path,
    task_type: str = "prospective_validation",
    project_id: str | None = None,
    campaign_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a minimal prospective evaluation task artifact."""

    resolved = Path(output_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": task_id,
        "task_type": task_type,
        "project_id": project_id,
        "campaign_id": campaign_id,
        "created_at": datetime.now(UTC).isoformat(),
        "metadata": dict(metadata or {}),
        "constraints": {
            "prospective_validation_is_not_clinical_validation": True,
            "outcomes_must_be_imported_results_or_fixtures": True,
            "predictions_must_be_frozen_before_outcomes": True,
        },
    }
    _write_json(resolved / TASK_PATH, task)
    return task


def freeze_prospective_run(
    *,
    task_id: str,
    predictions: ArtifactLike,
    output_dir: str | Path,
    prospective_run_id: str | None = None,
    prediction_set_id: str | None = None,
    model_or_pipeline_version: str = "unknown",
    project_id: str | None = None,
    campaign_id: str | None = None,
    frozen_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[ProspectiveValidationRun, FrozenPredictionSet]:
    """Freeze a candidate ranking, prediction, portfolio, or campaign decision artifact."""

    resolved = Path(output_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    frozen_time = _require_aware(frozen_at or datetime.now(UTC), "frozen_at")
    payload, source_path = _load_artifact(predictions)
    payload_hash = _artifact_hash(payload)
    records = _records(payload)
    candidate_ids = [_record_id(record) for record in records]
    candidate_ids = [candidate_id for candidate_id in candidate_ids if candidate_id]
    run_id = prospective_run_id or f"prospective-{task_id}"
    set_id = prediction_set_id or f"frozen-{task_id}"

    create_prospective_task(
        task_id=task_id,
        output_dir=resolved,
        project_id=project_id,
        campaign_id=campaign_id,
        metadata={"created_by": "prospective_freeze"},
    )
    frozen_set = FrozenPredictionSet(
        prediction_set_id=set_id,
        task_id=task_id,
        model_or_pipeline_version=model_or_pipeline_version,
        frozen_at=frozen_time,
        prediction_artifact_id=_artifact_id(payload, source_path, FROZEN_PAYLOAD_PATH),
        input_candidate_ids=candidate_ids,
        prediction_count=len(records),
        outcome_labels_available=False,
        outcome_artifact_id=None,
        metadata={
            "prediction_artifact_hash": payload_hash,
            "prediction_artifact_path": source_path,
            "frozen_payload_path": FROZEN_PAYLOAD_PATH,
            "artifact_kind": _artifact_kind(payload),
            "prospective_constraints": [
                "outcomes_imported_before_freeze_do_not_count_as_prospective",
                "modified_predictions_after_freeze_invalidate_run",
                "generated_molecules_require_exact_structure_match",
                "failed_qc_outcomes_are_not_positive_or_negative_evidence",
            ],
            **dict(metadata or {}),
        },
    )
    run = ProspectiveValidationRun(
        prospective_run_id=run_id,
        project_id=project_id,
        campaign_id=campaign_id,
        task_id=task_id,
        frozen_prediction_set_id=set_id,
        frozen_before_outcomes=True,
        outcome_imported_at=None,
        evaluation_report_id=None,
        status="awaiting_outcomes",
        warnings=[],
        metadata={
            "prediction_artifact_hash": payload_hash,
            "clinical_validation": False,
            "biomedical_evidence": False,
        },
    )

    _write_json(resolved / FROZEN_PAYLOAD_PATH, payload)
    _write_model(resolved / FROZEN_SET_PATH, frozen_set)
    _write_model(resolved / RUN_PATH, run)
    _write_json(
        resolved / HASHES_PATH,
        {
            "prediction_artifact_hash": payload_hash,
            "prediction_artifact_path": source_path,
            "locked_at": frozen_time.isoformat(),
        },
    )
    return run, frozen_set


def import_prospective_outcomes(
    run_dir: str | Path,
    *,
    outcomes: ArtifactLike,
    outcome_imported_at: datetime | None = None,
) -> ProspectiveValidationRun:
    """Import assay outcomes and mark whether they remain prospective."""

    resolved = Path(run_dir)
    run = _load_run(resolved)
    frozen_set = _load_frozen_set(resolved)
    payload, source_path = _load_artifact(outcomes)
    import_time = _require_aware(outcome_imported_at or datetime.now(UTC), "outcome_imported_at")
    outcome_hash = _artifact_hash(payload)
    records = _records(payload)
    time_warnings = _outcome_time_warnings(records, import_time, frozen_set.frozen_at)
    warnings = list(dict.fromkeys([*run.warnings, *time_warnings]))
    frozen_before_outcomes = "outcome_before_prediction_freeze" not in warnings
    status = (
        "outcomes_imported"
        if frozen_before_outcomes and run.status != "invalid"
        else "invalid"
    )

    frozen_set.outcome_labels_available = True
    frozen_set.outcome_artifact_id = _artifact_id(payload, source_path, OUTCOME_PAYLOAD_PATH)
    frozen_set.metadata["outcome_artifact_hash"] = outcome_hash
    frozen_set.metadata["outcome_artifact_path"] = source_path
    run.frozen_before_outcomes = frozen_before_outcomes
    run.outcome_imported_at = import_time
    run.status = status
    run.warnings = warnings
    run.metadata["outcome_artifact_hash"] = outcome_hash
    run.metadata["outcome_artifact_path"] = source_path

    _write_json(resolved / OUTCOME_PAYLOAD_PATH, payload)
    _write_model(resolved / FROZEN_SET_PATH, frozen_set)
    _write_model(resolved / RUN_PATH, run)
    hashes = _read_json(resolved / HASHES_PATH) if (resolved / HASHES_PATH).exists() else {}
    hashes["outcome_artifact_hash"] = outcome_hash
    hashes["outcome_artifact_path"] = source_path
    hashes["outcome_imported_at"] = import_time.isoformat()
    _write_json(resolved / HASHES_PATH, hashes)
    return run


def evaluate_prospective_run(run_dir: str | Path) -> EvaluationReport:
    """Evaluate frozen predictions against imported prospective outcomes."""

    resolved = Path(run_dir)
    run = _load_run(resolved)
    frozen_set = _load_frozen_set(resolved)
    predictions = _read_json(resolved / FROZEN_PAYLOAD_PATH)
    outcome_path = resolved / OUTCOME_PAYLOAD_PATH
    outcomes = _read_json(outcome_path) if outcome_path.exists() else {}
    warnings = list(run.warnings)

    if _prediction_hash_mismatch(frozen_set, predictions):
        warnings.append("prediction_hash_mismatch")
    if not outcomes:
        warnings.append("outcomes_not_imported")
    warnings = list(dict.fromkeys(warnings))
    valid = (
        run.frozen_before_outcomes
        and "prediction_hash_mismatch" not in warnings
        and bool(outcomes)
    )
    evaluated = _evaluate_exact_hits(predictions, outcomes)
    if evaluated.generated_structure_mismatch_count:
        warnings.append("generated_outcome_without_exact_structure_match")
    if evaluated.seed_result_reuse_count:
        warnings.append("generated_seed_result_not_counted")
    warnings = list(dict.fromkeys(warnings))
    status = "evaluated" if valid else "invalid"

    report = EvaluationReport(
        evaluation_id=f"{run.prospective_run_id}-evaluation",
        suite_id=None,
        task_id=run.task_id,
        dataset_id=f"{run.prospective_run_id}-prospective-outcomes",
        split_id=None,
        prediction_set_id=frozen_set.prediction_set_id,
        metrics=[
            EvaluationMetric(
                metric_id="prospective_validation_valid",
                name="prospective_validation_valid",
                metric_type="guardrail",
                value=valid,
                higher_is_better=True,
                metadata={"status": "computed"},
            ),
            EvaluationMetric(
                metric_id="prospective_exact_hit_rate",
                name="prospective_exact_hit_rate",
                metric_type="ranking",
                value=evaluated.hit_rate,
                higher_is_better=True,
                metadata={
                    "status": "computed" if evaluated.evaluable_count else "undefined",
                    "evaluable_count": evaluated.evaluable_count,
                    "hit_count": evaluated.hit_count,
                },
            ),
            EvaluationMetric(
                metric_id="prospective_selected_hit_rate",
                name="prospective_selected_hit_rate",
                metric_type="decision_quality",
                value=evaluated.selected_hit_rate,
                higher_is_better=True,
                metadata={
                    "status": "computed" if evaluated.selected_evaluable_count else "undefined",
                    "selected_evaluable_count": evaluated.selected_evaluable_count,
                    "selected_hit_count": evaluated.selected_hit_count,
                },
            ),
            EvaluationMetric(
                metric_id="failed_qc_outcome_exclusion_count",
                name="failed_qc_outcome_exclusion_count",
                metric_type="decision_quality",
                value=float(evaluated.failed_qc_count),
                higher_is_better=False,
                metadata={"status": "computed"},
            ),
        ],
        baseline_metrics=[],
        comparisons=[],
        warnings=warnings,
        limitations=[
            "Benchmark results are evaluation artifacts, not biomedical evidence.",
            "Prospective validation analytics are not clinical validation.",
            "Prospective hit metrics require imported assay outcomes or benchmark fixtures.",
            "Failed-QC outcomes are excluded from positive/negative evidence counts.",
            "Generated molecule outcomes require exact linked structure matching.",
        ],
        created_at=datetime.now(UTC),
        metadata={
            "prospective_status": status,
            "frozen_before_outcomes": run.frozen_before_outcomes,
            "clinical_validation": False,
            "biomedical_evidence": False,
            "prediction_hash_checked": True,
            "generated_exact_structure_matching_required": True,
        },
    )

    run.status = status
    run.warnings = warnings
    run.evaluation_report_id = report.evaluation_id
    _write_model(resolved / RUN_PATH, run)
    _write_json(resolved / REPORT_JSON_PATH, report.model_dump(mode="json"))
    (resolved / REPORT_MARKDOWN_PATH).write_text(
        render_prospective_validation_report(report),
        encoding="utf-8",
    )
    return report


class _ProspectiveEvaluation:
    def __init__(
        self,
        *,
        hit_rate: float | None,
        selected_hit_rate: float | None,
        evaluable_count: int,
        hit_count: int,
        selected_evaluable_count: int,
        selected_hit_count: int,
        failed_qc_count: int,
        generated_structure_mismatch_count: int,
        seed_result_reuse_count: int,
    ) -> None:
        self.hit_rate = hit_rate
        self.selected_hit_rate = selected_hit_rate
        self.evaluable_count = evaluable_count
        self.hit_count = hit_count
        self.selected_evaluable_count = selected_evaluable_count
        self.selected_hit_count = selected_hit_count
        self.failed_qc_count = failed_qc_count
        self.generated_structure_mismatch_count = generated_structure_mismatch_count
        self.seed_result_reuse_count = seed_result_reuse_count


def _evaluate_exact_hits(predictions: Any, outcomes: Any) -> _ProspectiveEvaluation:
    prediction_records = _records(predictions)
    outcome_records = _records(outcomes)
    failed_qc_count = sum(1 for outcome in outcome_records if _failed_qc(outcome))
    eligible_outcomes = [outcome for outcome in outcome_records if not _failed_qc(outcome)]
    hit_flags: list[bool] = []
    selected_hit_flags: list[bool] = []
    generated_structure_mismatch_count = 0
    seed_result_reuse_count = 0

    for prediction in prediction_records:
        outcome, mismatch, seed_reuse = _matching_outcome(prediction, eligible_outcomes)
        generated_structure_mismatch_count += int(mismatch)
        seed_result_reuse_count += int(seed_reuse)
        if outcome is None:
            continue
        label = _outcome_label(outcome)
        if label is None:
            continue
        hit_flags.append(label)
        if _selected(prediction):
            selected_hit_flags.append(label)

    hit_rate = _rate(hit_flags)
    selected_hit_rate = _rate(selected_hit_flags)
    return _ProspectiveEvaluation(
        hit_rate=hit_rate,
        selected_hit_rate=selected_hit_rate,
        evaluable_count=len(hit_flags),
        hit_count=sum(1 for flag in hit_flags if flag),
        selected_evaluable_count=len(selected_hit_flags),
        selected_hit_count=sum(1 for flag in selected_hit_flags if flag),
        failed_qc_count=failed_qc_count,
        generated_structure_mismatch_count=generated_structure_mismatch_count,
        seed_result_reuse_count=seed_result_reuse_count,
    )


def _matching_outcome(
    prediction: Mapping[str, Any],
    outcomes: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, bool, bool]:
    if _is_generated(prediction):
        generated_id = _string_value(prediction.get("generated_id"))
        seed_id = _string_value(
            prediction.get("seed_candidate_id") or prediction.get("seed_molecule_id")
        )
        structure = _structure_key(prediction)
        seed_reuse = False
        for outcome in outcomes:
            if generated_id and _string_value(outcome.get("generated_id")) == generated_id:
                if _structures_match(structure, _structure_key(outcome)):
                    return outcome, False, False
                return None, True, False
            if seed_id and _record_id(outcome) == seed_id:
                seed_reuse = True
        return None, False, seed_reuse
    record_id = _record_id(prediction)
    if not record_id:
        return None, False, False
    for outcome in outcomes:
        if _record_id(outcome) == record_id:
            return outcome, False, False
    return None, False, False


def _outcome_time_warnings(
    records: Sequence[Mapping[str, Any]],
    import_time: datetime,
    frozen_at: datetime,
) -> list[str]:
    warnings: list[str] = []
    for record in records:
        outcome_time = _record_time(record) or import_time
        if outcome_time <= frozen_at:
            warnings.append("outcome_before_prediction_freeze")
            break
    return warnings


def _prediction_hash_mismatch(frozen_set: FrozenPredictionSet, frozen_payload: Any) -> bool:
    expected = str(frozen_set.metadata.get("prediction_artifact_hash") or "")
    if not expected:
        return True
    source_path = frozen_set.metadata.get("prediction_artifact_path")
    if isinstance(source_path, str) and source_path:
        source = Path(source_path)
        if source.exists():
            payload, _ = _load_artifact(source)
            return _artifact_hash(payload) != expected
    return _artifact_hash(frozen_payload) != expected


def _load_artifact(artifact: ArtifactLike) -> tuple[Any, str | None]:
    if isinstance(artifact, str | Path):
        path = Path(artifact)
        return json.loads(path.read_text(encoding="utf-8")), str(path)
    return artifact, None


def _artifact_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Sequence) and not isinstance(payload, str | bytes | bytearray):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in (
        "candidate_rankings",
        "ranked_candidates",
        "predictions",
        "model_predictions",
        "portfolio_selections",
        "campaign_decisions",
        "candidates",
        "generated_candidates",
        "retained_generated_molecules",
        "assay_results",
        "results",
        "rows",
        "items",
    ):
        value = payload.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return [item for item in value if isinstance(item, Mapping)]
    return [payload]


def _record_id(record: Mapping[str, Any]) -> str | None:
    for key in ("generated_id", "candidate_id", "molecule_id", "compound_id", "id"):
        value = _string_value(record.get(key))
        if value:
            return value
    return None


def _artifact_id(payload: Any, source_path: str | None, default: str) -> str:
    if isinstance(payload, Mapping):
        value = _string_value(payload.get("artifact_id") or payload.get("id"))
        if value:
            return value
    return source_path or default


def _artifact_kind(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return "records"
    for key in (
        "candidate_rankings",
        "predictions",
        "portfolio_selections",
        "campaign_decisions",
        "model_predictions",
    ):
        if key in payload:
            return key
    return "artifact"


def _record_time(record: Mapping[str, Any]) -> datetime | None:
    for key in (
        "outcome_imported_at",
        "imported_at",
        "result_date",
        "created_at",
        "assay_date",
        "measured_at",
    ):
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _require_aware(value, "outcome timestamp")
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _require_aware(parsed, "outcome timestamp")


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _failed_qc(record: Mapping[str, Any]) -> bool:
    status = str(
        record.get("qc_status") or record.get("qc") or record.get("quality_status") or ""
    ).lower()
    return status in {"failed", "fail", "qc_failed", "invalid", "rejected"}


def _outcome_label(record: Mapping[str, Any]) -> bool | None:
    for key in ("outcome_label", "label", "result", "status", "is_hit", "active"):
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"positive", "active", "hit", "supported", "pass", "passed", "true"}:
            return True
        if normalized in {
            "negative",
            "inactive",
            "miss",
            "not_supported",
            "fail",
            "failed",
            "false",
        }:
            return False
    return None


def _is_generated(record: Mapping[str, Any]) -> bool:
    value = record.get("is_generated")
    if isinstance(value, bool):
        return value
    return bool(record.get("generated_id"))


def _structure_key(record: Mapping[str, Any]) -> str | None:
    for key in ("canonical_smiles", "inchi_key", "inchikey", "smiles"):
        value = _string_value(record.get(key))
        if value:
            return value
    return None


def _structures_match(prediction_key: str | None, outcome_key: str | None) -> bool:
    return prediction_key is not None and outcome_key is not None and prediction_key == outcome_key


def _selected(record: Mapping[str, Any]) -> bool:
    selected = record.get("selected")
    if isinstance(selected, bool):
        return selected
    status = str(record.get("selection_status") or record.get("decision") or "").strip().lower()
    return status in {"selected", "approved", "chosen", "advance", "advanced", "continue"}


def _rate(flags: Sequence[bool]) -> float | None:
    if not flags:
        return None
    return sum(1 for flag in flags if flag) / len(flags)


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_run(run_dir: Path) -> ProspectiveValidationRun:
    return ProspectiveValidationRun.model_validate(_read_json(run_dir / RUN_PATH))


def _load_frozen_set(run_dir: Path) -> FrozenPredictionSet:
    return FrozenPredictionSet.model_validate(_read_json(run_dir / FROZEN_SET_PATH))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_model(path: Path, model: FrozenPredictionSet | ProspectiveValidationRun) -> None:
    _write_json(path, model.model_dump(mode="json"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "FrozenPredictionSet",
    "ProspectiveValidationRun",
    "create_prospective_task",
    "evaluate_prospective_run",
    "freeze_prospective_run",
    "import_prospective_outcomes",
]
