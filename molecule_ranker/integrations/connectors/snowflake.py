from __future__ import annotations

import os
from importlib import import_module
from typing import Any

from molecule_ranker.integrations.connectors.base import ConnectorCallRecorder, ConnectorError
from molecule_ranker.integrations.connectors.warehouse import GenericWarehouseConnector
from molecule_ranker.integrations.schemas import ConnectorConfig


class SnowflakeConnector(GenericWarehouseConnector):
    connector_name = "snowflake"
    provider = "snowflake"
    capabilities = GenericWarehouseConnector.capabilities + (
        "snowflake_optional_dependency",
        "warehouse_database_schema_config",
    )
    limitations = GenericWarehouseConnector.limitations + (
        "Requires optional snowflake-connector-python dependency.",
        "Secrets are resolved in memory and are never included in logs or audit metadata.",
    )

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        recorder: ConnectorCallRecorder | None = None,
        credential_resolver: Any | None = None,
    ) -> None:
        super().__init__(config, recorder=recorder, credential_resolver=credential_resolver)
        self._connection: Any | None = None

    def _connect(self) -> Any:
        if self._connection is not None:
            return self._connection
        try:
            snowflake_connector = import_module("snowflake.connector")
        except ImportError as exc:
            raise ConnectorError(
                "Snowflake connector is not installed. Install the optional snowflake "
                "dependency group to enable this connector."
            ) from exc
        self._connection = snowflake_connector.connect(
            account=self._required_config("account"),
            user=self._required_config("user"),
            password=self._password(),
            warehouse=self.config.config.get("warehouse"),
            database=self.config.config.get("database"),
            schema=self.config.config.get("schema"),
            role=self.config.config.get("role"),
        )
        return self._connection

    def _execute_query(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        connection = self._connect()
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _required_config(self, key: str) -> str:
        value = self.config.config.get(key)
        if not value:
            raise ConnectorError(f"Snowflake config {key!r} is required.")
        return str(value)

    def _password(self) -> str:
        env_name = self.config.config.get("password_env") or "SNOWFLAKE_PASSWORD"
        value = os.environ.get(str(env_name))
        if value:
            return value
        if self.config.credential_ref and self.credential_resolver:
            return self.credential_resolver(self.config.credential_ref.credential_id)
        raise ConnectorError(f"Snowflake password env var {env_name} is not set.")


__all__ = ["SnowflakeConnector"]
