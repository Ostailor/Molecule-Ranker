from __future__ import annotations

import csv
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path

import pytest

from molecule_ranker.integrations.warehouse_models import (
    WAREHOUSE_SCHEMA_VERSION,
    WAREHOUSE_TABLES,
    ParquetUnavailableError,
    build_sql_insert_upsert,
    export_rows_csv,
    export_rows_parquet,
    generate_warehouse_schema,
    generate_warehouse_schema_manifest,
    list_warehouse_tables,
    normalize_export_rows,
)


def test_warehouse_schema_generation() -> None:
    expected = {
        "mr_project_runs",
        "mr_candidates",
        "mr_generated_molecules",
        "mr_targets",
        "mr_evidence_items",
        "mr_literature_claims",
        "mr_developability_assessments",
        "mr_assay_results",
        "mr_review_decisions",
        "mr_active_learning_suggestions",
        "mr_sync_jobs",
        "mr_artifacts",
    }

    assert set(list_warehouse_tables()) == expected
    manifest = generate_warehouse_schema_manifest()
    assert manifest["schema_version"] == WAREHOUSE_SCHEMA_VERSION
    assert len(manifest["tables"]) == len(expected)
    schema = generate_warehouse_schema("mr_candidates")
    columns = [column["name"] for column in schema["columns"]]
    assert columns[:3] == ["candidate_id", "run_id", "candidate_name"]
    assert "org_id" in columns
    assert "project_id" in columns
    assert "source_record_id" in columns
    assert "exported_at" in columns


def test_csv_export_uses_stable_columns_and_sanitizes_rows(tmp_path: Path) -> None:
    output_path = tmp_path / "candidates.csv"

    export_rows_csv(
        "mr_candidates",
        [
            {
                "org_id": "org-1",
                "project_id": "project-1",
                "candidate_id": "cand-1",
                "candidate_name": "Candidate A",
                "score": 0.91,
                "api_key": "sk-secretsecretsecret",
                "provenance_json": {"token": "secret-value", "source": "ranker"},
                "created_at": datetime(2026, 5, 27, tzinfo=UTC),
            }
        ],
        output_path,
    )

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["org_id"] == "org-1"
    assert rows[0]["project_id"] == "project-1"
    assert rows[0]["candidate_id"] == "cand-1"
    assert rows[0]["candidate_name"] == "Candidate A"
    assert rows[0]["score"] == "0.91"
    assert rows[0]["source_record_id"] == "cand-1"
    assert rows[0]["source_record_type"] == "mr_candidates"
    assert rows[0]["source_system"] == "molecule_ranker"
    assert rows[0]["created_at"] == "2026-05-27T00:00:00+00:00"
    assert rows[0]["exported_at"]
    assert rows[0]["provenance_json"] == '{"source": "ranker"}'
    csv_text = output_path.read_text()
    assert "sk-secret" not in csv_text
    assert "secret-value" not in csv_text


def test_optional_parquet_export_skips_if_dependency_missing(tmp_path: Path) -> None:
    if find_spec("pyarrow") is None:
        with pytest.raises(ParquetUnavailableError):
            export_rows_parquet(
                "mr_candidates",
                [{"org_id": "org-1", "candidate_id": "cand-1"}],
                tmp_path / "candidates.parquet",
            )
        pytest.skip("pyarrow is not installed")

    output = export_rows_parquet(
        "mr_candidates",
        [{"org_id": "org-1", "candidate_id": "cand-1"}],
        tmp_path / "candidates.parquet",
    )

    assert output.exists()


def test_no_secret_fields_and_unsafe_artifacts_are_excluded() -> None:
    for model in WAREHOUSE_TABLES.values():
        for column in model.columns:
            lowered = column.name.lower()
            assert "secret" not in lowered
            assert "password" not in lowered
            assert "token" not in lowered
            assert "credential" not in lowered
            assert "api_key" not in lowered

    rows = normalize_export_rows(
        "mr_artifacts",
        [
            {
                "org_id": "org-1",
                "artifact_id": "artifact-secret",
                "artifact_type": "secret",
                "path": "/tmp/.env",
            },
            {
                "org_id": "org-1",
                "artifact_id": "artifact-cache",
                "artifact_type": "cache",
                "path": "/tmp/.cache/payload.json",
            },
            {
                "org_id": "org-1",
                "artifact_id": "artifact-transcript",
                "artifact_type": "codex_transcript",
                "path": "/tmp/transcript.json",
            },
            {
                "org_id": "org-1",
                "artifact_id": "artifact-safe",
                "artifact_type": "report",
                "path": "/tmp/report.md",
            },
        ],
    )

    assert [row["artifact_id"] for row in rows] == ["artifact-safe"]


def test_sql_insert_upsert_generation() -> None:
    sql, params = build_sql_insert_upsert(
        "mr_assay_results",
        [
            {
                "org_id": "org-1",
                "project_id": "project-1",
                "assay_result_id": "result-1",
                "candidate_id": "cand-1",
                "source_record_id": "warehouse-result-1",
            }
        ],
    )

    assert sql.startswith("insert into mr_assay_results")
    assert "on conflict (assay_result_id) do update" in sql
    assert params[0]["assay_result_id"] == "result-1"
    assert params[0]["source_record_id"] == "warehouse-result-1"
