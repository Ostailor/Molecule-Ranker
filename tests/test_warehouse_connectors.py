from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.integrations.connectors import (
    DatabricksSqlConnector,
    GenericWarehouseConnector,
    SnowflakeConnector,
)
from molecule_ranker.integrations.connectors.base import ConnectorError
from molecule_ranker.integrations.connectors.warehouse import curated_export_schema
from molecule_ranker.integrations.schemas import ConnectorConfig


def test_generic_warehouse_mocked_import_query() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def executor(query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        seen.append((query, params))
        return [
            {
                "result_id": "res-1",
                "experiment_id": "exp-1",
                "assay_name": "Binding",
                "candidate_id": "cand-1",
                "outcome": "positive",
                "value": 12.3,
                "unit": "nM",
                "source_record_id": "warehouse-res-1",
            }
        ]

    connector = GenericWarehouseConnector(_warehouse_config(), query_executor=executor)

    imported = connector.import_query(
        "select * from assay_results where project_id = :project_id",
        {"project_id": "proj-1"},
    )

    assert seen == [
        ("select * from assay_results where project_id = :project_id", {"project_id": "proj-1"})
    ]
    assert imported[0]["external_ref"].external_record_id == "warehouse-res-1"
    assert imported[0]["assay_result"].validation_status == "valid"


def test_warehouse_parameterized_query_enforcement() -> None:
    connector = GenericWarehouseConnector(_warehouse_config(), query_executor=lambda _q, _p: [])

    with pytest.raises(ConnectorError, match="Missing SQL bind parameters"):
        connector.run_query_readonly(
            "select * from assay_results where project_id = :project_id",
            {},
        )

    with pytest.raises(ConnectorError, match="Unused SQL bind parameters"):
        connector.run_query_readonly("select * from assay_results", {"project_id": "proj-1"})


def test_warehouse_unsafe_sql_rejected() -> None:
    connector = GenericWarehouseConnector(_warehouse_config(), query_executor=lambda _q, _p: [])

    with pytest.raises(ConnectorError, match="read-only"):
        connector.run_query_readonly(
            "delete from assay_results where project_id = :project_id",
            {"project_id": "proj-1"},
        )

    with pytest.raises(ConnectorError, match="bound parameters"):
        connector.run_query_readonly("select * from assay_results where project_id = 'proj-1'")


def test_optional_dependencies_absent_fail_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> Any:
        raise ImportError("missing optional dependency")

    monkeypatch.setattr(
        "molecule_ranker.integrations.connectors.databricks.import_module",
        missing,
    )
    monkeypatch.setattr(
        "molecule_ranker.integrations.connectors.snowflake.import_module",
        missing,
    )

    with pytest.raises(ConnectorError, match="Databricks SQL connector is not installed"):
        DatabricksSqlConnector(_databricks_config()).connect()
    with pytest.raises(ConnectorError, match="Snowflake connector is not installed"):
        SnowflakeConnector(_snowflake_config()).connect()


def test_export_table_schema_valid() -> None:
    connector = GenericWarehouseConnector(_warehouse_config(write_enabled=True))

    exported = connector.export_table(
        "experimental_results",
        rows=[
            {
                "result_id": "res-1",
                "candidate_id": "cand-1",
                "assay_name": "Binding",
                "outcome": "positive",
                "value": 12.3,
                "unit": "nM",
                "source_record_id": "warehouse-res-1",
            }
        ],
    )

    schema = curated_export_schema("experimental_results")
    assert exported["schema"] == schema
    assert exported["row_count"] == 1
    assert schema["result_id"] == "string"
    assert schema["value"] == "number"


def _warehouse_config(*, write_enabled: bool = False) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="warehouse-test",
        name="Warehouse",
        provider="postgresql",
        kind="data_warehouse",
        mode="write_enabled" if write_enabled else "read_only",
        allow_writes=write_enabled,
        explicit_write_permission=write_enabled,
        config={"connection_url": "sqlite:///:memory:"},
    )


def _databricks_config() -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="databricks-test",
        name="Databricks",
        provider="databricks_sql",
        kind="data_warehouse",
        mode="read_only",
        config={
            "server_hostname": "example.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc",
        },
    )


def _snowflake_config() -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="snowflake-test",
        name="Snowflake",
        provider="snowflake",
        kind="data_warehouse",
        mode="read_only",
        config={"account": "acct", "user": "user", "warehouse": "wh"},
    )
