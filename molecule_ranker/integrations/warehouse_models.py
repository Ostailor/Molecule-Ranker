from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.credentials import redact_secret_values

WAREHOUSE_SCHEMA_VERSION = "mr_warehouse_v0.9.0"
WarehouseExportFormat = Literal["csv", "parquet", "sql"]

SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
CACHE_ARTIFACT_TYPES = {"cache", "pytest_cache", "codex_cache", "dependency_cache"}
SECRET_ARTIFACT_TYPES = {"env", "secret", "credential", "credentials", "private_key"}
COPYRIGHT_TEXT_FIELDS = {"abstract", "full_abstract", "full_text", "article_text", "body"}


class WarehouseModelError(ValueError):
    """Raised when curated warehouse export input is invalid."""


class ParquetUnavailableError(ImportError):
    """Raised when optional Parquet dependencies are not installed."""


@dataclass(frozen=True)
class WarehouseColumn:
    name: str
    data_type: str
    nullable: bool = True
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "description": self.description,
        }


@dataclass(frozen=True)
class WarehouseTableModel:
    table_name: str
    version: str
    primary_key: tuple[str, ...]
    columns: tuple[WarehouseColumn, ...]
    description: str = ""

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "version": self.version,
            "primary_key": list(self.primary_key),
            "description": self.description,
            "columns": [column.as_dict() for column in self.columns],
        }


PROVENANCE_COLUMNS = (
    WarehouseColumn("org_id", "string", False, "Organization scope."),
    WarehouseColumn("project_id", "string", True, "Project scope."),
    WarehouseColumn("source_system", "string", True, "Originating source system."),
    WarehouseColumn("source_record_id", "string", True, "Source system record identifier."),
    WarehouseColumn("source_record_type", "string", True, "Source system record type."),
    WarehouseColumn("source_record_version", "string", True, "Source system version."),
    WarehouseColumn("sync_job_id", "string", True, "Integration sync job identifier."),
    WarehouseColumn("sync_record_id", "string", True, "Integration sync record identifier."),
    WarehouseColumn("created_at", "timestamp", True, "Creation timestamp."),
    WarehouseColumn("updated_at", "timestamp", True, "Update timestamp."),
    WarehouseColumn("exported_at", "timestamp", False, "Warehouse export timestamp."),
)


def _cols(*columns: WarehouseColumn) -> tuple[WarehouseColumn, ...]:
    return (*columns, *PROVENANCE_COLUMNS)


WAREHOUSE_TABLES: dict[str, WarehouseTableModel] = {
    "mr_project_runs": WarehouseTableModel(
        table_name="mr_project_runs",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("run_id",),
        description="Molecule-ranker project run summaries.",
        columns=_cols(
            WarehouseColumn("run_id", "string", False),
            WarehouseColumn("run_status", "string", True),
            WarehouseColumn("disease_name", "string", True),
            WarehouseColumn("target_count", "integer", True),
            WarehouseColumn("candidate_count", "integer", True),
            WarehouseColumn("generated_candidate_count", "integer", True),
            WarehouseColumn("summary_json", "json", True),
        ),
    ),
    "mr_candidates": WarehouseTableModel(
        table_name="mr_candidates",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("candidate_id",),
        description="Ranked source-backed candidate records.",
        columns=_cols(
            WarehouseColumn("candidate_id", "string", False),
            WarehouseColumn("run_id", "string", True),
            WarehouseColumn("candidate_name", "string", True),
            WarehouseColumn("target_id", "string", True),
            WarehouseColumn("target_symbol", "string", True),
            WarehouseColumn("disease_id", "string", True),
            WarehouseColumn("disease_name", "string", True),
            WarehouseColumn("rank", "integer", True),
            WarehouseColumn("score", "number", True),
            WarehouseColumn("score_version", "string", True),
            WarehouseColumn("evidence_count", "integer", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_generated_molecules": WarehouseTableModel(
        table_name="mr_generated_molecules",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("generated_molecule_id",),
        description="Generated molecule hypotheses and identifiers.",
        columns=_cols(
            WarehouseColumn("generated_molecule_id", "string", False),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("run_id", "string", True),
            WarehouseColumn("molecule_name", "string", True),
            WarehouseColumn("canonical_smiles", "string", True),
            WarehouseColumn("inchi_key", "string", True),
            WarehouseColumn("hypothesis", "string", True),
            WarehouseColumn("generation_model", "string", True),
            WarehouseColumn("generation_artifact_id", "string", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_targets": WarehouseTableModel(
        table_name="mr_targets",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("target_id",),
        description="Target records used by ranked candidates.",
        columns=_cols(
            WarehouseColumn("target_id", "string", False),
            WarehouseColumn("target_symbol", "string", True),
            WarehouseColumn("target_name", "string", True),
            WarehouseColumn("disease_id", "string", True),
            WarehouseColumn("disease_name", "string", True),
            WarehouseColumn("organism", "string", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_evidence_items": WarehouseTableModel(
        table_name="mr_evidence_items",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("evidence_item_id",),
        description="Source-backed evidence items without full source documents.",
        columns=_cols(
            WarehouseColumn("evidence_item_id", "string", False),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("target_id", "string", True),
            WarehouseColumn("evidence_type", "string", True),
            WarehouseColumn("source_id", "string", True),
            WarehouseColumn("source_title", "string", True),
            WarehouseColumn("source_url", "string", True),
            WarehouseColumn("snippet", "string", True),
            WarehouseColumn("confidence", "number", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_literature_claims": WarehouseTableModel(
        table_name="mr_literature_claims",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("claim_id",),
        description="Literature claims and citation metadata, excluding full abstracts.",
        columns=_cols(
            WarehouseColumn("claim_id", "string", False),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("target_id", "string", True),
            WarehouseColumn("claim_text", "string", True),
            WarehouseColumn("claim_type", "string", True),
            WarehouseColumn("pmid", "string", True),
            WarehouseColumn("doi", "string", True),
            WarehouseColumn("citation_title", "string", True),
            WarehouseColumn("publication_year", "integer", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_developability_assessments": WarehouseTableModel(
        table_name="mr_developability_assessments",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("assessment_id",),
        description="Developability assessment summaries.",
        columns=_cols(
            WarehouseColumn("assessment_id", "string", False),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("generated_molecule_id", "string", True),
            WarehouseColumn("risk_level", "string", True),
            WarehouseColumn("summary", "string", True),
            WarehouseColumn("flags_json", "json", True),
            WarehouseColumn("assessed_by", "string", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_assay_results": WarehouseTableModel(
        table_name="mr_assay_results",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("assay_result_id",),
        description="Validated assay result summaries imported through V0.6 paths.",
        columns=_cols(
            WarehouseColumn("assay_result_id", "string", False),
            WarehouseColumn("experiment_id", "string", True),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("molecule_name", "string", True),
            WarehouseColumn("assay_name", "string", True),
            WarehouseColumn("endpoint_name", "string", True),
            WarehouseColumn("outcome", "string", True),
            WarehouseColumn("value", "number", True),
            WarehouseColumn("unit", "string", True),
            WarehouseColumn("result_date", "timestamp", True),
            WarehouseColumn("validation_status", "string", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_review_decisions": WarehouseTableModel(
        table_name="mr_review_decisions",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("review_decision_id",),
        description="Expert review decisions and provenance.",
        columns=_cols(
            WarehouseColumn("review_decision_id", "string", False),
            WarehouseColumn("review_item_id", "string", True),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("decision", "string", True),
            WarehouseColumn("decision_status", "string", True),
            WarehouseColumn("reviewer_id", "string", True),
            WarehouseColumn("decided_at", "timestamp", True),
            WarehouseColumn("rationale_summary", "string", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_active_learning_suggestions": WarehouseTableModel(
        table_name="mr_active_learning_suggestions",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("suggestion_id",),
        description="Active-learning suggestions and prioritization metadata.",
        columns=_cols(
            WarehouseColumn("suggestion_id", "string", False),
            WarehouseColumn("batch_id", "string", True),
            WarehouseColumn("candidate_id", "string", True),
            WarehouseColumn("generated_molecule_id", "string", True),
            WarehouseColumn("priority_score", "number", True),
            WarehouseColumn("suggestion_type", "string", True),
            WarehouseColumn("rationale", "string", True),
            WarehouseColumn("model_version", "string", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
    "mr_sync_jobs": WarehouseTableModel(
        table_name="mr_sync_jobs",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("sync_job_id",),
        description="Integration sync job summaries.",
        columns=_cols(
            WarehouseColumn("sync_job_id", "string", False),
            WarehouseColumn("external_system_id", "string", True),
            WarehouseColumn("direction", "string", True),
            WarehouseColumn("object_types_json", "json", True),
            WarehouseColumn("mode", "string", True),
            WarehouseColumn("status", "string", True),
            WarehouseColumn("requested_by_user_id", "string", True),
            WarehouseColumn("started_at", "timestamp", True),
            WarehouseColumn("completed_at", "timestamp", True),
            WarehouseColumn("records_seen", "integer", True),
            WarehouseColumn("records_imported", "integer", True),
            WarehouseColumn("records_exported", "integer", True),
            WarehouseColumn("records_skipped", "integer", True),
            WarehouseColumn("records_failed", "integer", True),
            WarehouseColumn("error_summary", "string", True),
        ),
    ),
    "mr_artifacts": WarehouseTableModel(
        table_name="mr_artifacts",
        version=WAREHOUSE_SCHEMA_VERSION,
        primary_key=("artifact_id",),
        description=(
            "Artifact manifest records excluding secrets, caches, and unsanitized transcripts."
        ),
        columns=_cols(
            WarehouseColumn("artifact_id", "string", False),
            WarehouseColumn("run_id", "string", True),
            WarehouseColumn("artifact_type", "string", True),
            WarehouseColumn("path", "string", True),
            WarehouseColumn("sha256", "string", True),
            WarehouseColumn("size_bytes", "integer", True),
            WarehouseColumn("provenance_json", "json", True),
        ),
    ),
}

TABLE_ALIASES = {
    "project_runs": "mr_project_runs",
    "candidates": "mr_candidates",
    "generated_molecules": "mr_generated_molecules",
    "targets": "mr_targets",
    "evidence_items": "mr_evidence_items",
    "literature_claims": "mr_literature_claims",
    "developability_assessments": "mr_developability_assessments",
    "experimental_results": "mr_assay_results",
    "assay_results": "mr_assay_results",
    "review_decisions": "mr_review_decisions",
    "active_learning_suggestions": "mr_active_learning_suggestions",
    "sync_jobs": "mr_sync_jobs",
    "artifact_manifest": "mr_artifacts",
    "artifacts": "mr_artifacts",
}


def list_warehouse_tables() -> list[str]:
    return list(WAREHOUSE_TABLES)


def resolve_table_name(table_name: str) -> str:
    resolved = TABLE_ALIASES.get(table_name, table_name)
    if resolved not in WAREHOUSE_TABLES:
        allowed = ", ".join(sorted(WAREHOUSE_TABLES))
        raise WarehouseModelError(
            f"Unsupported warehouse table {table_name!r}. Allowed: {allowed}."
        )
    return resolved


def get_warehouse_table_model(table_name: str) -> WarehouseTableModel:
    return WAREHOUSE_TABLES[resolve_table_name(table_name)]


def generate_warehouse_schema(table_name: str) -> dict[str, Any]:
    return get_warehouse_table_model(table_name).as_dict()


def generate_warehouse_schema_manifest() -> dict[str, Any]:
    return {
        "schema_version": WAREHOUSE_SCHEMA_VERSION,
        "tables": [model.as_dict() for model in WAREHOUSE_TABLES.values()],
    }


def curated_export_schema(table_name: str) -> dict[str, str]:
    model = get_warehouse_table_model(table_name)
    return {column.name: column.data_type for column in model.columns}


def normalize_export_rows(
    table_name: str,
    rows: list[dict[str, Any]],
    *,
    include_codex_transcripts: bool = False,
    exported_at: datetime | None = None,
) -> list[dict[str, Any]]:
    model = get_warehouse_table_model(table_name)
    now = exported_at or datetime.now(UTC)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if _skip_row(model.table_name, row, include_codex_transcripts=include_codex_transcripts):
            continue
        normalized.append(_normalize_row(model, row, now))
    return normalized


def export_rows_csv(
    table_name: str,
    rows: list[dict[str, Any]],
    path: str | Path,
    *,
    include_codex_transcripts: bool = False,
) -> Path:
    model = get_warehouse_table_model(table_name)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_export_rows(
        model.table_name,
        rows,
        include_codex_transcripts=include_codex_transcripts,
    )
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=model.column_names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(normalized)
    return output_path


def export_rows_parquet(
    table_name: str,
    rows: list[dict[str, Any]],
    path: str | Path,
    *,
    include_codex_transcripts: bool = False,
) -> Path:
    try:
        import pyarrow as pa  # type: ignore[import-not-found]
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ParquetUnavailableError(
            "Parquet export requires optional dependency pyarrow."
        ) from exc

    model = get_warehouse_table_model(table_name)
    normalized = normalize_export_rows(
        model.table_name,
        rows,
        include_codex_transcripts=include_codex_transcripts,
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(normalized, schema=_pyarrow_schema(model, pa))
    pq.write_table(table, output_path)
    return output_path


def build_sql_insert_upsert(
    table_name: str,
    rows: list[dict[str, Any]],
    *,
    conflict_columns: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    model = get_warehouse_table_model(table_name)
    normalized = normalize_export_rows(model.table_name, rows)
    columns = model.column_names
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f":{column}" for column in columns)
    sql = f"insert into {model.table_name} ({column_sql}) values ({value_sql})"
    conflicts = conflict_columns or list(model.primary_key)
    if conflicts:
        update_columns = [column for column in columns if column not in set(conflicts)]
        update_sql = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
        sql = f"{sql} on conflict ({', '.join(conflicts)}) do update set {update_sql}"
    return sql, normalized


def export_rows_sql(
    connector: Any,
    table_name: str,
    rows: list[dict[str, Any]],
    *,
    conflict_columns: list[str] | None = None,
) -> dict[str, Any]:
    sql, params = build_sql_insert_upsert(
        table_name,
        rows,
        conflict_columns=conflict_columns,
    )
    if hasattr(connector, "export_table"):
        return connector.export_table(
            resolve_table_name(table_name),
            rows=params,
            sql=sql,
            conflict_columns=conflict_columns,
        )
    if hasattr(connector, "execute_many"):
        connector.execute_many(sql, params)
        return {
            "status": "exported",
            "table_name": resolve_table_name(table_name),
            "row_count": len(params),
        }
    raise WarehouseModelError("Connector does not support SQL export/upsert.")


def _normalize_row(
    model: WarehouseTableModel,
    row: dict[str, Any],
    exported_at: datetime,
) -> dict[str, Any]:
    sanitized = _sanitize_json(row)
    output: dict[str, Any] = {}
    for column in model.column_names:
        value = sanitized.get(column)
        if column == "exported_at":
            value = value or exported_at
        if column == "source_record_id":
            value = value or _source_record_id(sanitized, model)
        if column == "source_record_type":
            value = value or model.table_name
        if column == "source_system":
            value = value or sanitized.get("external_system_id") or "molecule_ranker"
        output[column] = _serialize_value(value)
    return output


def _skip_row(
    table_name: str,
    row: dict[str, Any],
    *,
    include_codex_transcripts: bool,
) -> bool:
    if table_name != "mr_artifacts":
        return False
    artifact_type = str(row.get("artifact_type") or "").lower()
    path = str(row.get("path") or "").lower()
    if artifact_type in CACHE_ARTIFACT_TYPES | SECRET_ARTIFACT_TYPES:
        return True
    if any(marker in path for marker in ("/.cache/", "__pycache__", ".pytest_cache")):
        return True
    if artifact_type == "codex_transcript" and not include_codex_transcripts:
        return True
    return False


def _source_record_id(row: dict[str, Any], model: WarehouseTableModel) -> str | None:
    for key in [
        "source_record_id",
        "external_record_id",
        *model.primary_key,
        "result_id",
        "id",
    ]:
        value = row.get(key)
        if value not in (None, "", []):
            return str(value)
    return None


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if any(marker in lowered for marker in SECRET_FIELD_MARKERS):
                continue
            if lowered in COPYRIGHT_TEXT_FIELDS:
                continue
            sanitized[key] = _sanitize_json(raw_value)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    redacted = redact_secret_values(redact_secrets(value))
    if len(redacted) > 2000:
        return redacted[:1997] + "..."
    return redacted


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _pyarrow_schema(model: WarehouseTableModel, pa: Any) -> Any:
    fields = []
    for column in model.columns:
        fields.append(pa.field(column.name, _pyarrow_type(column.data_type, pa)))
    return pa.schema(fields)


def _pyarrow_type(data_type: str, pa: Any) -> Any:
    if data_type == "integer":
        return pa.int64()
    if data_type == "number":
        return pa.float64()
    if data_type == "timestamp":
        return pa.string()
    return pa.string()


__all__ = [
    "ParquetUnavailableError",
    "WAREHOUSE_SCHEMA_VERSION",
    "WAREHOUSE_TABLES",
    "WarehouseColumn",
    "WarehouseExportFormat",
    "WarehouseModelError",
    "WarehouseTableModel",
    "build_sql_insert_upsert",
    "curated_export_schema",
    "export_rows_csv",
    "export_rows_parquet",
    "export_rows_sql",
    "generate_warehouse_schema",
    "generate_warehouse_schema_manifest",
    "get_warehouse_table_model",
    "list_warehouse_tables",
    "normalize_export_rows",
    "resolve_table_name",
]
