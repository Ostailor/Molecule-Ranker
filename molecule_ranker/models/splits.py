"""Leakage-aware model split helpers."""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

from molecule_ranker.models.features import LEAKAGE_FEATURE_COLUMNS

SplitStrategy = Literal[
    "auto",
    "random",
    "scaffold",
    "time_based",
    "project_based",
    "external_holdout",
]


@dataclass(frozen=True)
class ModelSplitResult:
    strategy: str
    assignments: list[dict[str, Any]]
    leakage_check_report: dict[str, Any]
    assignment_path: Path | None = None


def build_model_splits(
    rows: Sequence[Any],
    *,
    feature_rows: Sequence[Mapping[str, Any]] | None = None,
    strategy: SplitStrategy | None = None,
    output_dir: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> ModelSplitResult:
    """Create deterministic split assignments and a leakage-check report."""

    config = dict(config or {})
    prepared_rows = [_prepare_row(row, index) for index, row in enumerate(rows)]
    selected_strategy = _select_strategy(prepared_rows, strategy, config)
    assignments = _assign_rows(prepared_rows, selected_strategy, config)
    leakage_report = validate_split_leakage(
        assignments,
        feature_rows=feature_rows or [],
        split_strategy=selected_strategy,
    )
    assignment_path = None
    if output_dir is not None:
        assignment_path = Path(output_dir) / f"{selected_strategy}_split_assignments.json"
        assignment_path.parent.mkdir(parents=True, exist_ok=True)
        assignment_path.write_text(
            json.dumps(
                {
                    "strategy": selected_strategy,
                    "assignments": assignments,
                    "leakage_check_report": leakage_report,
                },
                indent=2,
                sort_keys=True,
                default=_json_default,
            )
            + "\n"
        )
    return ModelSplitResult(
        strategy=selected_strategy,
        assignments=assignments,
        leakage_check_report=leakage_report,
        assignment_path=assignment_path,
    )


def validate_split_leakage(
    assignments: Sequence[Mapping[str, Any]],
    *,
    feature_rows: Sequence[Mapping[str, Any]],
    split_strategy: str | None = None,
) -> dict[str, Any]:
    checks = {
        "inchi_key_overlap": _overlap_check(assignments, "inchi_key"),
        "generated_id_overlap": _overlap_check(assignments, "generated_id"),
        "duplicate_assay_result_id": _duplicate_result_id_check(assignments),
        "future_train_result_date": _future_train_date_check(assignments, split_strategy),
        "canonical_smiles_overlap": _overlap_check(assignments, "canonical_smiles"),
        "feature_label_leakage": _feature_label_leakage_check(feature_rows),
    }
    failed = [name for name, check in checks.items() if not check["passed"]]
    return {
        "passed": not failed,
        "failed_checks": failed,
        "checks": checks,
    }


def _assign_rows(
    rows: Sequence[dict[str, Any]],
    strategy: str,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if strategy == "scaffold":
        return _scaffold_split(rows, config)
    if strategy == "time_based":
        return _time_based_split(rows, config)
    if strategy == "project_based":
        return _project_based_split(rows, config)
    if strategy == "external_holdout":
        return _external_holdout_split(rows, config)
    return _random_split(rows, config)


def _random_split(
    rows: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    seed = int(config.get("seed", 0) or 0)
    test_count = _desired_test_count(len(rows), config)
    row_ids = [row["row_id"] for row in rows]
    shuffled = row_ids[:]
    random.Random(seed).shuffle(shuffled)
    test_ids = set(shuffled[:test_count])
    return [_with_split(row, "test" if row["row_id"] in test_ids else "train") for row in rows]


def _scaffold_split(
    rows: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    test_target = _desired_test_count(len(rows), config)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("scaffold") or row["row_id"])].append(row)

    test_ids: set[str] = set()
    for _scaffold, group in sorted(groups.items(), key=lambda item: _stable_sort_key(item[0])):
        if len(test_ids) >= test_target:
            break
        test_ids.update(str(row["row_id"]) for row in group)
        if len(test_ids) >= test_target and len(test_ids) < len(rows):
            break

    if len(test_ids) == len(rows) and len(groups) > 1:
        _scaffold, group = sorted(groups.items(), key=lambda item: _stable_sort_key(item[0]))[-1]
        test_ids.difference_update(str(row["row_id"]) for row in group)

    return [_with_split(row, "test" if row["row_id"] in test_ids else "train") for row in rows]


def _time_based_split(
    rows: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    test_count = _desired_test_count(len(rows), config)
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            _date_sort_value(row.get("result_date")),
            str(row.get("row_id") or ""),
        ),
    )
    test_ids = {row["row_id"] for row in sorted_rows[-test_count:]} if test_count else set()
    return [_with_split(row, "test" if row["row_id"] in test_ids else "train") for row in rows]


def _project_based_split(
    rows: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    holdout_projects = {str(project) for project in config.get("holdout_project_ids", [])}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        project_id = str(row.get("project_id") or "unknown_project")
        groups[project_id].append(row)

    if not holdout_projects:
        test_target = _desired_test_count(len(rows), config)
        sorted_projects = sorted(groups.items(), key=lambda item: _stable_sort_key(item[0]))
        for project_id, _group in sorted_projects:
            if sum(len(groups[project]) for project in holdout_projects) >= test_target:
                break
            holdout_projects.add(project_id)
            if sum(len(groups[project]) for project in holdout_projects) >= test_target:
                break

    return [
        _with_split(row, "test" if str(row.get("project_id")) in holdout_projects else "train")
        for row in rows
    ]


def _external_holdout_split(
    rows: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    holdout_result_ids = {str(result_id) for result_id in config.get("holdout_result_ids", [])}
    holdout_candidate_ids = {
        str(candidate_id) for candidate_id in config.get("holdout_candidate_ids", [])
    }
    assignments = []
    for row in rows:
        is_holdout = (
            bool(row.get("external_holdout")) or str(row.get("result_id")) in holdout_result_ids
        )
        is_holdout = is_holdout or str(row.get("candidate_id")) in holdout_candidate_ids
        assignments.append(_with_split(row, "test" if is_holdout else "train"))
    return assignments


def _select_strategy(
    rows: Sequence[dict[str, Any]],
    strategy: SplitStrategy | None,
    config: Mapping[str, Any],
) -> str:
    if strategy and strategy != "auto":
        return strategy
    if _dates_reliable(rows, config):
        return "time_based"
    if any(row.get("canonical_smiles") for row in rows):
        return "scaffold"
    return "random"


def _prepare_row(row: Any, index: int) -> dict[str, Any]:
    canonical_smiles = _optional_string(_value(row, "canonical_smiles") or _value(row, "smiles"))
    return {
        "row_id": _row_id(row, index),
        "result_id": _optional_string(
            _value(row, "result_id")
            or _value(row, "assay_result_id")
            or _value(row, "source_result_id")
        ),
        "candidate_id": _optional_string(_value(row, "candidate_id")),
        "generated_id": _optional_string(_value(row, "generated_id")),
        "inchi_key": _optional_string(_value(row, "inchi_key") or _value(row, "inchikey")),
        "canonical_smiles": canonical_smiles,
        "scaffold": _scaffold_for_smiles(canonical_smiles),
        "result_date": _parse_date(_value(row, "result_date")),
        "project_id": _optional_string(_value(row, "project_id")),
        "external_holdout": bool(_value(row, "external_holdout")),
    }


def _with_split(row: Mapping[str, Any], split: str) -> dict[str, Any]:
    return {**row, "split": split}


def _desired_test_count(row_count: int, config: Mapping[str, Any]) -> int:
    if row_count <= 1:
        return 0
    fraction = float(config.get("test_fraction", 0.2) or 0.0)
    count = int(round(row_count * fraction))
    if fraction > 0:
        count = max(1, count)
    return min(max(count, 0), row_count - 1)


def _overlap_check(assignments: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    train_values = _values_for_split(assignments, key, "train")
    test_values = _values_for_split(assignments, key, "test")
    overlap = sorted(train_values.intersection(test_values))
    return {"passed": not overlap, "values": overlap}


def _duplicate_result_id_check(assignments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(
        str(result_id)
        for assignment in assignments
        if (result_id := assignment.get("result_id")) not in {None, ""}
    )
    duplicates = sorted(result_id for result_id, count in counts.items() if count > 1)
    return {"passed": not duplicates, "values": duplicates}


def _future_train_date_check(
    assignments: Sequence[Mapping[str, Any]],
    split_strategy: str | None,
) -> dict[str, Any]:
    if split_strategy != "time_based":
        return {"passed": True, "train_max_date": None, "test_min_date": None}
    train_dates = [
        parsed
        for row in assignments
        if row.get("split") == "train"
        and (parsed := _parse_date(row.get("result_date"))) is not None
    ]
    test_dates = [
        parsed
        for row in assignments
        if row.get("split") == "test"
        and (parsed := _parse_date(row.get("result_date"))) is not None
    ]
    if not train_dates or not test_dates:
        return {"passed": True, "train_max_date": None, "test_min_date": None}
    train_max = max(train_dates)
    test_min = min(test_dates)
    return {
        "passed": train_max <= test_min,
        "train_max_date": train_max.isoformat(),
        "test_min_date": test_min.isoformat(),
    }


def _feature_label_leakage_check(feature_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    leakage_columns: set[str] = set()
    for row in feature_rows:
        features = row.get("features")
        if not isinstance(features, Mapping):
            continue
        leakage_columns.update(str(key) for key in features if str(key) in LEAKAGE_FEATURE_COLUMNS)
    columns = sorted(leakage_columns)
    return {"passed": not columns, "columns": columns}


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


def _dates_reliable(rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> bool:
    if bool(config.get("result_dates_reliable", False)):
        return all(row.get("result_date") is not None for row in rows)
    return False


def _scaffold_for_smiles(smiles: str | None) -> str | None:
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    scaffold_mol = MurckoScaffold.GetScaffoldForMol(mol)
    scaffold = Chem.MolToSmiles(scaffold_mol, canonical=True, isomericSmiles=True)
    if scaffold:
        return scaffold
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)


def _row_id(row: Any, index: int) -> str:
    value = _value(row, "row_id") or _value(row, "candidate_id") or _value(row, "result_id")
    return str(value or f"row-{index}")


def _value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _date_sort_value(value: Any) -> tuple[int, str]:
    parsed = _parse_date(value)
    if parsed is None:
        return (1, "")
    return (0, parsed.isoformat())


def _stable_sort_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


__all__ = [
    "ModelSplitResult",
    "SplitStrategy",
    "build_model_splits",
    "validate_split_leakage",
]
