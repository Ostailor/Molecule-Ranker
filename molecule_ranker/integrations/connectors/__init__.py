from __future__ import annotations

from typing import Any

from molecule_ranker.integrations.connectors.base import (
    AssayConnector,
    BaseConnector,
    ConnectorCallRecorder,
    ConnectorError,
    ELNConnector,
    ExternalConnector,
    RegistryConnector,
    WarehouseConnector,
    WebhookConnector,
)
from molecule_ranker.integrations.connectors.benchling import BenchlingConnector
from molecule_ranker.integrations.connectors.databricks import DatabricksSqlConnector
from molecule_ranker.integrations.connectors.generic_file import (
    GenericCsvSftpConnector,
    GenericFileConnector,
)
from molecule_ranker.integrations.connectors.generic_rest import GenericRESTConnector
from molecule_ranker.integrations.connectors.sila import SiLAMetadataAdapter
from molecule_ranker.integrations.connectors.snowflake import SnowflakeConnector
from molecule_ranker.integrations.connectors.warehouse import (
    GenericWarehouseConnector,
    PostgreSQLWarehouseConnector,
)
from molecule_ranker.integrations.schemas import ConnectorConfig

CONNECTOR_CLASSES: dict[str, type[BaseConnector]] = {
    "benchling": BenchlingConnector,
    "generic_rest": GenericRESTConnector,
    "generic_csv_sftp": GenericFileConnector,
    "postgresql": PostgreSQLWarehouseConnector,
    "databricks_sql": DatabricksSqlConnector,
    "snowflake": SnowflakeConnector,
    "sila_metadata": SiLAMetadataAdapter,
}


def create_connector(config: ConnectorConfig) -> BaseConnector:
    return CONNECTOR_CLASSES[config.provider](config)


def connector_catalog() -> list[dict[str, Any]]:
    catalog = []
    for provider, connector_cls in CONNECTOR_CLASSES.items():
        catalog.append(
            {
                "provider": provider,
                "capabilities": list(connector_cls.capabilities),
                "limitations": list(connector_cls.limitations),
                "default_mode": "dry_run",
            }
        )
    return catalog


__all__ = [
    "BaseConnector",
    "BenchlingConnector",
    "CONNECTOR_CLASSES",
    "AssayConnector",
    "ConnectorCallRecorder",
    "ConnectorError",
    "DatabricksSqlConnector",
    "ELNConnector",
    "ExternalConnector",
    "GenericCsvSftpConnector",
    "GenericFileConnector",
    "GenericRESTConnector",
    "GenericWarehouseConnector",
    "PostgreSQLWarehouseConnector",
    "RegistryConnector",
    "SiLAMetadataAdapter",
    "SnowflakeConnector",
    "WarehouseConnector",
    "WebhookConnector",
    "connector_catalog",
    "create_connector",
]
