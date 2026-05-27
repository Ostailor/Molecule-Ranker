from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text

from molecule_ranker.experimental.importers import validate_assay_results
from molecule_ranker.experimental.schemas import AssayResult
from molecule_ranker.integrations.connectors.base import (
    ConnectorCallRecorder,
    ConnectorError,
    WarehouseConnector,
)
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.schemas import ConnectorConfig, ExternalRecordRef
from molecule_ranker.integrations.warehouse_models import (
    curated_export_schema as curated_warehouse_export_schema,
)

CredentialResolver = Callable[[str], str]
QueryExecutor = Callable[[str, dict[str, Any]], list[dict[str, Any]]]

CURATED_EXPORT_SCHEMAS: dict[str, dict[str, str]] = {
    "candidates": {
        "candidate_id": "string",
        "candidate_name": "string",
        "target_id": "string",
        "disease_id": "string",
        "source_run_id": "string",
        "created_at": "datetime",
    },
    "generated_molecules": {
        "generated_molecule_id": "string",
        "candidate_id": "string",
        "canonical_smiles": "string",
        "inchi_key": "string",
        "hypothesis": "string",
        "created_at": "datetime",
    },
    "developability_assessments": {
        "assessment_id": "string",
        "candidate_id": "string",
        "risk_level": "string",
        "summary": "string",
        "created_at": "datetime",
    },
    "experimental_results": {
        "result_id": "string",
        "candidate_id": "string",
        "assay_name": "string",
        "outcome": "string",
        "value": "number",
        "unit": "string",
        "source_record_id": "string",
    },
    "review_decisions": {
        "review_item_id": "string",
        "candidate_id": "string",
        "decision": "string",
        "reviewer_id": "string",
        "decided_at": "datetime",
    },
    "active_learning_suggestions": {
        "suggestion_id": "string",
        "candidate_id": "string",
        "priority_score": "number",
        "rationale": "string",
        "created_at": "datetime",
    },
    "project_runs": {
        "run_id": "string",
        "project_id": "string",
        "status": "string",
        "created_at": "datetime",
        "completed_at": "datetime",
    },
    "artifact_manifest": {
        "artifact_id": "string",
        "project_id": "string",
        "artifact_type": "string",
        "sha256": "string",
        "created_at": "datetime",
    },
}

READONLY_SQL_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)
UNSAFE_SQL_TOKENS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|merge|copy|grant|revoke|call|execute)\b",
    re.IGNORECASE,
)
BIND_PARAM_RE = re.compile(r"(?<!:):[A-Za-z_][A-Za-z0-9_]*")
LITERAL_FILTER_RE = re.compile(r"(=|<|>|<=|>=|<>|!=)\s*('[^']*'|\d+(?:\.\d+)?)")


class GenericWarehouseConnector(WarehouseConnector):
    connector_name = "generic-warehouse"
    provider = "postgresql"
    capabilities = (
        "warehouse_readonly_query",
        "parameterized_query",
        "query_allowlist",
        "curated_table_export",
        "assay_result_import",
    )
    limitations = WarehouseConnector.limitations + (
        "Read-only SQL is enforced for import and readonly query paths.",
        "User-supplied values must be bound parameters; SQL string interpolation is rejected.",
        "Hosted mode requires query allowlist entries.",
    )

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        recorder: ConnectorCallRecorder | None = None,
        query_executor: QueryExecutor | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        effective_config = config
        if config.mode == "dry_run" and not config.allow_writes:
            effective_config = config.model_copy(update={"mode": "read_only"})
        super().__init__(effective_config, recorder=recorder)
        self.query_executor = query_executor
        self.credential_resolver = credential_resolver
        self._engine: Any | None = None

    def _connect(self) -> Any:
        if self.query_executor is not None:
            return self.query_executor
        if self._engine is None:
            self._engine = create_engine(self._connection_url(), future=True)
        return self._engine

    def _validate_connection(self) -> bool:
        if self.query_executor is not None:
            return True
        self._execute_query("select 1 as ok", {})
        return True

    def _export_table(
        self,
        table_name: str,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        schema = curated_export_schema(table_name)
        row_count = len(rows or [])
        return {
            "external_ref": ExternalRecordRef(
                external_system_id=self.config.connector_id,
                external_record_type="warehouse_export",
                external_record_id=f"{table_name}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                retrieved_at=datetime.now(UTC),
                metadata={"provider": self.provider},
            ),
            "table_name": table_name,
            "schema": schema,
            "row_count": row_count,
            "status": "exported" if self.config.mode == "write_enabled" else "dry_run",
        }

    def _import_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        query_name: str | None = None,
        object_type: str = "assay_result",
    ) -> list[dict[str, Any]]:
        rows = self._run_safe_query(query, params or {}, query_name=query_name)
        if object_type in {"assay_result", "experimental_results"}:
            results = [_assay_result_from_row(row, self.config.connector_id) for row in rows]
            report = validate_assay_results(results)
            if report.invalid_count or report.incomplete_count:
                raise ConnectorError("Warehouse assay result import failed V0.6 validation.")
            return [
                {
                    "external_ref": _row_ref(row, self.config.connector_id, "assay_result"),
                    "assay_result": result,
                }
                for row, result in zip(rows, results, strict=True)
            ]
        return [
            {
                "external_ref": _row_ref(row, self.config.connector_id, object_type),
                "payload": row,
            }
            for row in rows
        ]

    def _run_query_readonly(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        query_name: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._run_safe_query(query, params or {}, query_name=query_name)
        return [
            {
                "external_ref": _row_ref(row, self.config.connector_id, "warehouse_row"),
                "payload": row,
            }
            for row in rows
        ]

    def _run_safe_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        query_name: str | None,
    ) -> list[dict[str, Any]]:
        _validate_readonly_parameterized_query(query, params)
        self._enforce_query_allowlist(query, query_name=query_name)
        return self._execute_query(query, params)

    def _execute_query(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.query_executor is not None:
            return self.query_executor(query, params)
        engine = self._connect()
        with engine.connect() as connection:
            rows = connection.execute(text(query), params).mappings().fetchall()
        return [dict(row) for row in rows]

    def _enforce_query_allowlist(self, query: str, *, query_name: str | None) -> None:
        if not self.config.config.get("hosted_mode", False):
            return
        allowlist = dict(self.config.config.get("query_allowlist") or {})
        if not query_name or query_name not in allowlist:
            raise ConnectorError("Hosted warehouse queries must use a named allowlist entry.")
        if _normalize_sql(query) != _normalize_sql(str(allowlist[query_name])):
            raise ConnectorError("Warehouse query does not match the configured allowlist.")

    def _connection_url(self) -> str:
        if self.config.config.get("connection_url_env"):
            env_name = str(self.config.config["connection_url_env"])
            value = os.environ.get(env_name)
            if not value:
                raise ConnectorError(f"Warehouse connection URL env var {env_name} is not set.")
            return value
        if self.config.config.get("connection_url"):
            return str(self.config.config["connection_url"])
        if self.config.credential_ref and self.credential_resolver:
            return self.credential_resolver(self.config.credential_ref.credential_id)
        raise ConnectorError("Warehouse connection URL is not configured.")


class PostgreSQLWarehouseConnector(GenericWarehouseConnector):
    connector_name = "postgresql-warehouse"
    provider = "postgresql"


def curated_export_schema(table_name: str) -> dict[str, str]:
    if table_name in CURATED_EXPORT_SCHEMAS:
        return dict(CURATED_EXPORT_SCHEMAS[table_name])
    try:
        return curated_warehouse_export_schema(table_name)
    except ValueError as exc:
        raise ConnectorError(str(exc)) from exc


def _validate_readonly_parameterized_query(query: str, params: dict[str, Any]) -> None:
    stripped = query.strip()
    if ";" in stripped.rstrip(";"):
        raise ConnectorError("Warehouse query must contain a single statement.")
    if not READONLY_SQL_RE.match(stripped):
        raise ConnectorError("Warehouse queries must be read-only SELECT/WITH statements.")
    if UNSAFE_SQL_TOKENS.search(stripped):
        raise ConnectorError("Unsafe SQL token is not allowed in warehouse query.")
    placeholders = set(match.group(0)[1:] for match in BIND_PARAM_RE.finditer(stripped))
    missing = placeholders - set(params)
    if missing:
        raise ConnectorError(f"Missing SQL bind parameters: {sorted(missing)}")
    extra = set(params) - placeholders
    if extra:
        raise ConnectorError(f"Unused SQL bind parameters: {sorted(extra)}")
    if LITERAL_FILTER_RE.search(stripped):
        raise ConnectorError("User-supplied filter values must use bound parameters.")


def _normalize_sql(query: str) -> str:
    return " ".join(query.strip().rstrip(";").split()).lower()


def _row_ref(row: dict[str, Any], connector_id: str, record_type: str) -> ExternalRecordRef:
    row_id = (
        row.get("source_record_id")
        or row.get("external_record_id")
        or row.get("result_id")
        or row.get("id")
    )
    if not row_id:
        digest = redact_secret_values(str(sorted(row.items())))
        row_id = f"row-{abs(hash(digest))}"
    return ExternalRecordRef(
        external_system_id=connector_id,
        external_record_type=record_type,
        external_record_id=str(row_id),
        retrieved_at=datetime.now(UTC),
        metadata={},
    )


def _assay_result_from_row(row: dict[str, Any], connector_id: str) -> AssayResult:
    source_id = (
        row.get("source_record_id")
        or row.get("external_record_id")
        or row.get("result_id")
        or row.get("id")
    )
    return AssayResult(
        experiment_id=_string_or_none(row.get("experiment_id")),
        assay_name=_string_or_none(row.get("assay_name")),
        candidate_id=_string_or_none(row.get("candidate_id")),
        molecule_name=_string_or_none(row.get("molecule_name")),
        outcome=_string_or_none(row.get("outcome") or row.get("outcome_label")),  # type: ignore[arg-type]
        value=_float_or_none(row.get("value") or row.get("measured_value")),
        unit=_string_or_none(row.get("unit")),
        provenance={
            "source_type": "connected_system",
            "source_system": connector_id,
            "source_record_id": str(source_id or ""),
        },
        imported_at=datetime.now(UTC),
    )


def _string_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


__all__ = [
    "CURATED_EXPORT_SCHEMAS",
    "GenericWarehouseConnector",
    "PostgreSQLWarehouseConnector",
    "curated_export_schema",
]
