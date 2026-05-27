from __future__ import annotations

import os
from importlib import import_module
from typing import Any

from molecule_ranker.integrations.connectors.base import ConnectorCallRecorder, ConnectorError
from molecule_ranker.integrations.connectors.warehouse import GenericWarehouseConnector
from molecule_ranker.integrations.schemas import ConnectorConfig


class DatabricksSqlConnector(GenericWarehouseConnector):
    connector_name = "databricks-sql"
    provider = "databricks_sql"
    capabilities = GenericWarehouseConnector.capabilities + (
        "databricks_sql_optional_dependency",
        "query_tags",
    )
    limitations = GenericWarehouseConnector.limitations + (
        "Requires optional databricks-sql-connector dependency.",
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
            sql_module = import_module("databricks.sql")
        except ImportError as exc:
            raise ConnectorError(
                "Databricks SQL connector is not installed. Install the optional "
                "databricks dependency group to enable this connector."
            ) from exc
        self._connection = sql_module.connect(
            server_hostname=self._required_config("server_hostname"),
            http_path=self._required_config("http_path"),
            access_token=self._access_token(),
        )
        return self._connection

    def _execute_query(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        tagged_query = self._tag_query(query)
        connection = self._connect()
        cursor = connection.cursor()
        try:
            cursor.execute(tagged_query, params)
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _tag_query(self, query: str) -> str:
        tags = {
            "project_id": self.config.config.get("project_id"),
            "sync_job_id": self.config.config.get("sync_job_id"),
        }
        active = {key: value for key, value in tags.items() if value}
        if not active:
            return query
        tag_comment = " ".join(f"{key}={value}" for key, value in sorted(active.items()))
        return f"/* molecule_ranker {tag_comment} */\n{query}"

    def _required_config(self, key: str) -> str:
        value = self.config.config.get(key)
        if not value:
            raise ConnectorError(f"Databricks config {key!r} is required.")
        return str(value)

    def _access_token(self) -> str:
        env_name = self.config.config.get("token_env") or "DATABRICKS_TOKEN"
        value = os.environ.get(str(env_name))
        if value:
            return value
        if self.config.credential_ref and self.credential_resolver:
            return self.credential_resolver(self.config.credential_ref.credential_id)
        raise ConnectorError(f"Databricks token env var {env_name} is not set.")


__all__ = ["DatabricksSqlConnector"]
