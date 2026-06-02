from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, Literal

try:
    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold
except ImportError:  # pragma: no cover - rdkit is a project dependency.
    Chem = None
    MurckoScaffold = None

from molecule_ranker.evaluation.schemas import BenchmarkDataset, BenchmarkSplit

SplitStrategy = Literal[
    "random",
    "scaffold",
    "time_based",
    "project_based",
    "prospective",
    "external_holdout",
]

LEAKAGE_FEATURE_COLUMNS = {
    "label",
    "labels",
    "outcome",
    "outcome_label",
    "measured_value",
    "activity_label",
    "assay_result",
    "assay_result_id",
    "review_decision_after_outcome",
    "portfolio_decision_after_outcome",
    "future_result_count",
}


def build_random_split(
    dataset: BenchmarkDataset,
    *,
    split_id: str | None = None,
    seed: int = 18,
    validation_fraction: float = 0.2,
    test_fraction: float = 0.2,
) -> BenchmarkSplit:
    rows = _dataset_rows(dataset)
    row_ids = [row["row_id"] for row in rows]
    shuffled = row_ids[:]
    random.Random(seed).shuffle(shuffled)
    validation_count, test_count = _holdout_counts(
        len(row_ids),
        validation_fraction=validation_fraction,
        test_fraction=test_fraction,
    )
    validation_ids = set(shuffled[:validation_count])
    test_ids = set(shuffled[validation_count : validation_count + test_count])
    assignments = _assign_by_ids(rows, validation_ids=validation_ids, test_ids=test_ids)
    return _build_split(
        dataset,
        assignments,
        split_id=split_id or f"{dataset.dataset_id}:random-split",
        split_type="random",
        metadata={"seed": seed},
    )


def build_scaffold_split(
    dataset: BenchmarkDataset,
    *,
    split_id: str | None = None,
    validation_fraction: float = 0.2,
    test_fraction: float = 0.2,
) -> BenchmarkSplit:
    rows = _dataset_rows(dataset)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_scaffold_key(row)].append(row)
    validation_ids, test_ids = _group_holdouts(
        groups,
        validation_target=_fraction_count(len(rows), validation_fraction),
        test_target=_fraction_count(len(rows), test_fraction),
    )
    assignments = _assign_by_ids(rows, validation_ids=validation_ids, test_ids=test_ids)
    return _build_split(
        dataset,
        assignments,
        split_id=split_id or f"{dataset.dataset_id}:scaffold-split",
        split_type="scaffold",
        metadata={
            "scaffold_group_count": len(groups),
            "preferred_for_molecule_prediction_with_structures": True,
        },
    )


def build_time_based_split(
    dataset: BenchmarkDataset,
    *,
    split_id: str | None = None,
    validation_fraction: float = 0.2,
    test_fraction: float = 0.2,
) -> BenchmarkSplit:
    rows = _dataset_rows(dataset)
    if not _dates_reliable(rows):
        raise ValueError("time-based split requires reliable dates for every row")
    sorted_rows = sorted(rows, key=lambda row: (_row_date(row), row["row_id"]))
    validation_count, test_count = _holdout_counts(
        len(rows),
        validation_fraction=validation_fraction,
        test_fraction=test_fraction,
    )
    test_rows = sorted_rows[-test_count:] if test_count else []
    validation_start = max(0, len(sorted_rows) - test_count - validation_count)
    validation_rows = sorted_rows[validation_start : len(sorted_rows) - test_count]
    assignments = _assign_by_ids(
        rows,
        validation_ids={row["row_id"] for row in validation_rows},
        test_ids={row["row_id"] for row in test_rows},
    )
    return _build_split(
        dataset,
        assignments,
        split_id=split_id or f"{dataset.dataset_id}:time-based-split",
        split_type="time_based",
        metadata={"date_field_required": True},
    )


def build_project_based_split(
    dataset: BenchmarkDataset,
    *,
    split_id: str | None = None,
    holdout_project_ids: Sequence[str] | None = None,
    test_fraction: float = 0.2,
) -> BenchmarkSplit:
    rows = _dataset_rows(dataset)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_row_project_id(row)].append(row)
    holdouts = {str(project_id) for project_id in holdout_project_ids or []}
    if not holdouts:
        target = _fraction_count(len(rows), test_fraction)
        for project_id, _group in sorted(groups.items(), key=lambda item: _stable_key(item[0])):
            if sum(len(groups[item]) for item in holdouts) >= target:
                break
            holdouts.add(project_id)
            if sum(len(groups[item]) for item in holdouts) >= target:
                break
    assignments = [
        _assignment(row, "test" if _row_project_id(row) in holdouts else "train") for row in rows
    ]
    return _build_split(
        dataset,
        assignments,
        split_id=split_id or f"{dataset.dataset_id}:project-based-split",
        split_type="project_based",
        metadata={"holdout_project_ids": sorted(holdouts)},
    )


def build_prospective_split(
    dataset: BenchmarkDataset,
    *,
    split_id: str | None = None,
    frozen_prediction_artifact_ids: Sequence[str],
    outcome_label_artifact_ids: Sequence[str] | None = None,
    frozen_at: datetime | None = None,
) -> BenchmarkSplit:
    if not frozen_prediction_artifact_ids:
        raise ValueError("prospective split requires frozen prediction artifacts")
    rows = _dataset_rows(dataset)
    outcome_ids = set(outcome_label_artifact_ids or [])
    label_artifact_ids = set(dataset.metadata.get("label_artifact_ids", []))
    if outcome_ids and not outcome_ids.issubset(label_artifact_ids):
        raise ValueError("outcome labels must be imported after frozen predictions")
    assignments = [_assignment(row, "test") for row in rows]
    resolved_frozen_at = frozen_at or datetime.now(UTC)
    return _build_split(
        dataset,
        assignments,
        split_id=split_id or f"{dataset.dataset_id}:prospective-split",
        split_type="prospective",
        frozen_at=resolved_frozen_at,
        metadata={
            "frozen_prediction_artifact_ids": list(frozen_prediction_artifact_ids),
            "outcome_label_artifact_ids": sorted(outcome_ids),
            "predictions_frozen_before_outcomes": True,
        },
    )


def build_external_holdout_split(
    dataset: BenchmarkDataset,
    *,
    split_id: str | None = None,
    holdout_artifact_ids: Sequence[str] | None = None,
    holdout_row_ids: Sequence[str] | None = None,
) -> BenchmarkSplit:
    rows = _dataset_rows(dataset)
    artifact_ids = {str(item) for item in holdout_artifact_ids or []}
    row_ids = {str(item) for item in holdout_row_ids or []}
    assignments = [
        _assignment(
            row,
            "test"
            if row["row_id"] in row_ids or row.get("source_artifact_id") in artifact_ids
            else "train",
        )
        for row in rows
    ]
    return _build_split(
        dataset,
        assignments,
        split_id=split_id or f"{dataset.dataset_id}:external-holdout-split",
        split_type="external_holdout",
        metadata={
            "holdout_artifact_ids": sorted(artifact_ids),
            "holdout_row_ids": sorted(row_ids),
        },
    )


def build_benchmark_split(
    dataset: BenchmarkDataset,
    *,
    strategy: SplitStrategy,
    **kwargs: Any,
) -> BenchmarkSplit:
    if strategy == "random":
        return build_random_split(dataset, **kwargs)
    if strategy == "scaffold":
        return build_scaffold_split(dataset, **kwargs)
    if strategy == "time_based":
        return build_time_based_split(dataset, **kwargs)
    if strategy == "project_based":
        return build_project_based_split(dataset, **kwargs)
    if strategy == "prospective":
        return build_prospective_split(dataset, **kwargs)
    return build_external_holdout_split(dataset, **kwargs)


def recommended_split_strategy(dataset: BenchmarkDataset) -> SplitStrategy:
    rows = _dataset_rows(dataset)
    task_type = dataset.metadata.get("task_type")
    if task_type in {"surrogate_prediction", "developability_triage"} and any(
        _row_smiles(row) for row in rows
    ):
        return "scaffold"
    if _dates_reliable(rows):
        return "time_based"
    return "random"


def validate_split_leakage(
    assignments: Sequence[Mapping[str, Any]],
    *,
    split_type: str | None = None,
) -> dict[str, Any]:
    checks = {
        "same_inchikey_across_train_test": _overlap_check(assignments, "inchi_key"),
        "same_generated_id_across_train_test": _overlap_check(assignments, "generated_id"),
        "same_assay_result_duplicated": _duplicate_assay_result_check(assignments),
        "same_canonical_smiles_across_train_test": _overlap_check(
            assignments,
            "canonical_smiles",
        ),
        "future_result_leakage_into_train": _future_result_leakage_check(assignments),
        "label_column_leakage_into_features": _feature_leakage_check(assignments),
        "generated_analog_labeled_from_seed_result": _generated_seed_label_check(assignments),
        "review_decision_after_outcome_used_as_pre_outcome_feature": _post_outcome_feature_check(
            assignments,
            "review_decision",
        ),
        "portfolio_decision_after_outcome_used_as_pre_outcome_feature": (
            _post_outcome_feature_check(assignments, "portfolio_decision")
        ),
    }
    failed = [name for name, check in checks.items() if not check["passed"]]
    return {
        "passed": not failed,
        "split_type": split_type,
        "failed_checks": failed,
        "checks": checks,
    }


def _build_split(
    dataset: BenchmarkDataset,
    assignments: list[dict[str, Any]],
    *,
    split_id: str,
    split_type: SplitStrategy,
    frozen_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> BenchmarkSplit:
    leakage_checks = validate_split_leakage(assignments, split_type=split_type)
    return BenchmarkSplit(
        split_id=split_id,
        dataset_id=dataset.dataset_id,
        split_type=split_type,
        train_ids=_ids_for_split(assignments, "train"),
        validation_ids=_ids_for_split(assignments, "validation"),
        test_ids=_ids_for_split(assignments, "test"),
        frozen_at=frozen_at or datetime.now(UTC),
        leakage_checks=leakage_checks,
        metadata={**dict(metadata or {}), "assignments": assignments},
    )


def _dataset_rows(dataset: BenchmarkDataset) -> list[dict[str, Any]]:
    rows = dataset.metadata.get("rows", [])
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _assignment(row: Mapping[str, Any], split: str) -> dict[str, Any]:
    record = _row_record(row)
    return {
        **dict(row),
        "split": split,
        "inchi_key": _first_value(row, record, ("inchi_key", "inchikey")),
        "generated_id": _first_value(row, record, ("generated_id",)),
        "canonical_smiles": _first_value(row, record, ("canonical_smiles", "smiles")),
        "assay_result_id": _assay_result_id(row),
        "result_date": _row_date(row),
        "features": _row_features(row),
        "source_seed_result_id": _first_value(row, record, ("source_seed_result_id",)),
        "label_source_record_ids": _label_source_record_ids(row),
        "review_decision_date": _parse_datetime(
            _first_value(row, record, ("review_decision_date",))
        ),
        "portfolio_decision_date": _parse_datetime(
            _first_value(row, record, ("portfolio_decision_date",))
        ),
        "outcome_imported_at": _earliest_label_datetime(row),
    }


def _assign_by_ids(
    rows: Sequence[Mapping[str, Any]],
    *,
    validation_ids: set[str],
    test_ids: set[str],
) -> list[dict[str, Any]]:
    assignments = []
    for row in rows:
        row_id = str(row["row_id"])
        if row_id in test_ids:
            split = "test"
        elif row_id in validation_ids:
            split = "validation"
        else:
            split = "train"
        assignments.append(_assignment(row, split))
    return assignments


def _ids_for_split(assignments: Sequence[Mapping[str, Any]], split: str) -> list[str]:
    return [str(row["row_id"]) for row in assignments if row.get("split") == split]


def _holdout_counts(
    row_count: int,
    *,
    validation_fraction: float,
    test_fraction: float,
) -> tuple[int, int]:
    if row_count <= 1:
        return (0, 0)
    validation_count = _fraction_count(row_count, validation_fraction)
    test_count = _fraction_count(row_count, test_fraction)
    if validation_count + test_count >= row_count:
        overflow = validation_count + test_count - row_count + 1
        test_count = max(0, test_count - overflow)
    return validation_count, test_count


def _fraction_count(row_count: int, fraction: float) -> int:
    if row_count <= 1 or fraction <= 0:
        return 0
    return min(max(1, int(round(row_count * fraction))), row_count - 1)


def _group_holdouts(
    groups: Mapping[str, list[dict[str, Any]]],
    *,
    validation_target: int,
    test_target: int,
) -> tuple[set[str], set[str]]:
    validation_ids: set[str] = set()
    test_ids: set[str] = set()
    sorted_groups = sorted(groups.items(), key=lambda item: _stable_key(item[0]))
    for _group_key, rows in sorted_groups:
        target = test_ids if len(test_ids) < test_target else validation_ids
        target_limit = test_target if target is test_ids else validation_target
        if len(target) >= target_limit:
            continue
        target.update(str(row["row_id"]) for row in rows)
    if len(test_ids) + len(validation_ids) == sum(len(rows) for rows in groups.values()):
        validation_ids.clear()
    return validation_ids, test_ids


def _overlap_check(assignments: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    train_values = _values_for_split(assignments, key, "train")
    test_values = _values_for_split(assignments, key, "test")
    overlap = sorted(train_values.intersection(test_values))
    return {"passed": not overlap, "values": overlap}


def _duplicate_assay_result_check(assignments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(
        str(value)
        for assignment in assignments
        if (value := assignment.get("assay_result_id")) not in {None, ""}
    )
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    return {"passed": not duplicates, "values": duplicates}


def _future_result_leakage_check(assignments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    test_dates = [
        parsed
        for row in assignments
        if row.get("split") == "test" and (parsed := _parse_datetime(row.get("result_date")))
    ]
    if not test_dates:
        return {"passed": True, "train_rows_after_test_min": []}
    test_min = min(test_dates)
    leaked = [
        str(row["row_id"])
        for row in assignments
        if row.get("split") == "train"
        and (parsed := _parse_datetime(row.get("result_date")))
        and parsed > test_min
    ]
    return {"passed": not leaked, "train_rows_after_test_min": leaked}


def _feature_leakage_check(assignments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    leaked: dict[str, list[str]] = {}
    for row in assignments:
        features = row.get("features")
        if not isinstance(features, Mapping):
            continue
        columns = sorted(str(key) for key in features if str(key) in LEAKAGE_FEATURE_COLUMNS)
        if columns:
            leaked[str(row["row_id"])] = columns
    return {"passed": not leaked, "rows": leaked}


def _generated_seed_label_check(assignments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    leaked = []
    for row in assignments:
        source_seed_result_id = row.get("source_seed_result_id")
        label_ids = set(row.get("label_source_record_ids", []))
        if row.get("generated_id") and source_seed_result_id and source_seed_result_id in label_ids:
            leaked.append(str(row["row_id"]))
    return {"passed": not leaked, "rows": leaked}


def _post_outcome_feature_check(
    assignments: Sequence[Mapping[str, Any]],
    decision_kind: str,
) -> dict[str, Any]:
    date_key = f"{decision_kind}_date"
    feature_key = f"{decision_kind}_after_outcome"
    leaked = []
    for row in assignments:
        features = row.get("features")
        if isinstance(features, Mapping) and features.get(feature_key):
            leaked.append(str(row["row_id"]))
            continue
        decision_date = _parse_datetime(row.get(date_key))
        outcome_date = _parse_datetime(row.get("outcome_imported_at"))
        if decision_date is not None and outcome_date is not None and decision_date > outcome_date:
            leaked.append(str(row["row_id"]))
    return {"passed": not leaked, "rows": leaked}


def _values_for_split(
    assignments: Sequence[Mapping[str, Any]],
    key: str,
    split: str,
) -> set[str]:
    return {
        str(value)
        for row in assignments
        if row.get("split") == split and (value := row.get(key)) not in {None, ""}
    }


def _dates_reliable(rows: Sequence[Mapping[str, Any]]) -> bool:
    return bool(rows) and all(_row_date(row) is not None for row in rows)


def _row_date(row: Mapping[str, Any]) -> datetime | None:
    record = _row_record(row)
    value = _first_value(
        row,
        record,
        (
            "result_date",
            "created_at",
            "imported_at",
            "completed_at",
            "decision_date",
        ),
    )
    return _parse_datetime(value)


def _row_project_id(row: Mapping[str, Any]) -> str:
    record = _row_record(row)
    return str(_first_value(row, record, ("project_id", "program_id")) or "unknown_project")


def _row_smiles(row: Mapping[str, Any]) -> str | None:
    record = _row_record(row)
    value = _first_value(row, record, ("canonical_smiles", "smiles"))
    return str(value) if value not in {None, ""} else None


def _row_features(row: Mapping[str, Any]) -> Mapping[str, Any]:
    record = row.get("record")
    if isinstance(record, Mapping):
        features = record.get("features")
        if isinstance(features, Mapping):
            return features
    features = row.get("features")
    return features if isinstance(features, Mapping) else {}


def _assay_result_id(row: Mapping[str, Any]) -> str | None:
    labels = row.get("labels")
    if isinstance(labels, list) and labels:
        first = labels[0]
        if isinstance(first, Mapping):
            value = first.get("source_record_id") or first.get("result_id")
            return str(value) if value not in {None, ""} else None
    record = _row_record(row)
    value = _first_value(row, record, ("assay_result_id", "result_id", "source_result_id"))
    return str(value) if value not in {None, ""} else None


def _row_record(row: Mapping[str, Any]) -> Mapping[str, Any]:
    record = row.get("record")
    return record if isinstance(record, Mapping) else {}


def _label_source_record_ids(row: Mapping[str, Any]) -> list[str]:
    labels = row.get("labels")
    if not isinstance(labels, list):
        return []
    values = []
    for label in labels:
        if not isinstance(label, Mapping):
            continue
        value = label.get("source_record_id") or label.get("result_id")
        if value not in {None, ""}:
            values.append(str(value))
    return values


def _earliest_label_datetime(row: Mapping[str, Any]) -> datetime | None:
    labels = row.get("labels")
    if not isinstance(labels, list):
        return None
    parsed = [
        value
        for label in labels
        if isinstance(label, Mapping)
        and (
            value := _parse_datetime(
                label.get("imported_at") or label.get("result_date") or label.get("created_at")
            )
        )
    ]
    return min(parsed) if parsed else None


def _first_value(
    row: Mapping[str, Any],
    record: Mapping[str, Any],
    keys: Sequence[str],
) -> Any:
    for key in keys:
        if row.get(key) not in {None, ""}:
            return row[key]
        if record.get(key) not in {None, ""}:
            return record[key]
    return None


def _scaffold_key(row: Mapping[str, Any]) -> str:
    smiles = _row_smiles(row)
    scaffold = _scaffold_for_smiles(smiles)
    return scaffold or smiles or str(row["row_id"])


def _scaffold_for_smiles(smiles: str | None) -> str | None:
    if not smiles or Chem is None or MurckoScaffold is None:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    scaffold_mol = MurckoScaffold.GetScaffoldForMol(mol)
    scaffold = Chem.MolToSmiles(scaffold_mol, canonical=True, isomericSmiles=True)
    return scaffold or Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None


def _stable_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "BenchmarkSplit",
    "SplitStrategy",
    "build_benchmark_split",
    "build_external_holdout_split",
    "build_project_based_split",
    "build_prospective_split",
    "build_random_split",
    "build_scaffold_split",
    "build_time_based_split",
    "recommended_split_strategy",
    "validate_split_leakage",
]
