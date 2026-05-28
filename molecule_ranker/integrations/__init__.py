"""V1.0 external research-system integration framework."""

from typing import TYPE_CHECKING

from molecule_ranker.integrations.connectors import (
    AssayConnector,
    BaseConnector,
    BenchlingConnector,
    ConnectorCallRecorder,
    ConnectorError,
    DatabricksSqlConnector,
    ELNConnector,
    ExternalConnector,
    GenericCsvSftpConnector,
    GenericRESTConnector,
    GenericWarehouseConnector,
    PostgreSQLWarehouseConnector,
    RegistryConnector,
    SiLAMetadataAdapter,
    SnowflakeConnector,
    WarehouseConnector,
    WebhookConnector,
    connector_catalog,
    create_connector,
)
from molecule_ranker.integrations.exporters import (
    GENERATED_MOLECULE_WARNING,
    ExportPackageOptions,
    ExportPackageResult,
    ExportPermissionError,
    build_export_package,
    create_export_package,
    package_manifest,
    render_package_markdown,
)
from molecule_ranker.integrations.mapping import (
    codex_suggest_mapping,
    confirm_codex_mapping,
    map_assay_result_to_external,
    map_candidate_to_registry_entry,
    map_generated_molecule_to_external,
    map_review_item_to_external,
    validate_mapping_suggestions,
)
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    ConnectorHealth,
    DataContract,
    DataContractValidationReport,
    EntityMapping,
    ExternalIdMapping,
    ExternalRecordEnvelope,
    ExternalRecordProvenance,
    ExternalRecordRef,
    ExternalSystem,
    IntegrationAuditEvent,
    IntegrationCredential,
    IntegrationCredentialCreate,
    IntegrationCredentialRef,
    MappingSuggestionReport,
    MappingSuggestionRequest,
    SyncJob,
    SyncJobRecord,
    SyncRecord,
    WebhookIngestRequest,
)
from molecule_ranker.integrations.validation import (
    export_contract,
    import_contract,
    infer_contract_from_sample,
    normalize_record,
    validate_data_contract,
    validate_record_against_contract,
)
from molecule_ranker.integrations.warehouse_models import (
    WAREHOUSE_SCHEMA_VERSION,
    WAREHOUSE_TABLES,
    ParquetUnavailableError,
    WarehouseColumn,
    WarehouseModelError,
    WarehouseTableModel,
    build_sql_insert_upsert,
    export_rows_csv,
    export_rows_parquet,
    export_rows_sql,
    generate_warehouse_schema,
    generate_warehouse_schema_manifest,
    get_warehouse_table_model,
    list_warehouse_tables,
    normalize_export_rows,
)

if TYPE_CHECKING:
    from molecule_ranker.integrations.codex_assistant import (
        CodexIntegrationArtifact,
        CodexIntegrationAssistant,
        detect_prohibited_integration_actions,
    )
    from molecule_ranker.integrations.store import IntegrationStore
    from molecule_ranker.integrations.sync import SyncEngine, SyncRequest, run_sync
    from molecule_ranker.integrations.worker import (
        IntegrationWorker,
        enqueue_connector_health_check_job,
        enqueue_integration_sync_job,
        recommend_safe_connector_task,
    )

__all__ = [
    "BaseConnector",
    "BenchlingConnector",
    "AssayConnector",
    "ConnectorConfig",
    "ConnectorCallRecorder",
    "ConnectorError",
    "ConnectorHealth",
    "CodexIntegrationArtifact",
    "CodexIntegrationAssistant",
    "DataContract",
    "DataContractValidationReport",
    "DatabricksSqlConnector",
    "ELNConnector",
    "EntityMapping",
    "ExportPackageOptions",
    "ExportPackageResult",
    "ExportPermissionError",
    "ExternalIdMapping",
    "ExternalConnector",
    "ExternalRecordEnvelope",
    "ExternalRecordProvenance",
    "ExternalRecordRef",
    "ExternalSystem",
    "GenericCsvSftpConnector",
    "GENERATED_MOLECULE_WARNING",
    "GenericRESTConnector",
    "GenericWarehouseConnector",
    "IntegrationAuditEvent",
    "IntegrationCredential",
    "IntegrationCredentialCreate",
    "IntegrationCredentialRef",
    "IntegrationStore",
    "IntegrationWorker",
    "MappingSuggestionReport",
    "MappingSuggestionRequest",
    "PostgreSQLWarehouseConnector",
    "ParquetUnavailableError",
    "RegistryConnector",
    "SiLAMetadataAdapter",
    "SnowflakeConnector",
    "WarehouseConnector",
    "WAREHOUSE_SCHEMA_VERSION",
    "WAREHOUSE_TABLES",
    "WarehouseColumn",
    "WarehouseModelError",
    "WarehouseTableModel",
    "WebhookConnector",
    "SyncJob",
    "SyncJobRecord",
    "SyncRecord",
    "SyncEngine",
    "SyncRequest",
    "WebhookIngestRequest",
    "connector_catalog",
    "build_export_package",
    "codex_suggest_mapping",
    "confirm_codex_mapping",
    "create_connector",
    "create_export_package",
    "detect_prohibited_integration_actions",
    "enqueue_connector_health_check_job",
    "enqueue_integration_sync_job",
    "build_sql_insert_upsert",
    "export_contract",
    "export_rows_csv",
    "export_rows_parquet",
    "export_rows_sql",
    "generate_warehouse_schema",
    "generate_warehouse_schema_manifest",
    "get_warehouse_table_model",
    "import_contract",
    "infer_contract_from_sample",
    "list_warehouse_tables",
    "map_assay_result_to_external",
    "map_candidate_to_registry_entry",
    "map_generated_molecule_to_external",
    "map_review_item_to_external",
    "normalize_record",
    "normalize_export_rows",
    "package_manifest",
    "recommend_safe_connector_task",
    "render_package_markdown",
    "run_sync",
    "validate_data_contract",
    "validate_mapping_suggestions",
    "validate_record_against_contract",
]


def __getattr__(name: str) -> object:
    if name == "IntegrationStore":
        from molecule_ranker.integrations.store import IntegrationStore

        return IntegrationStore
    if name in {
        "CodexIntegrationArtifact",
        "CodexIntegrationAssistant",
        "detect_prohibited_integration_actions",
    }:
        from molecule_ranker.integrations import codex_assistant

        return getattr(codex_assistant, name)
    if name in {"SyncEngine", "SyncRequest", "run_sync"}:
        from molecule_ranker.integrations import sync

        return getattr(sync, name)
    if name in {
        "IntegrationWorker",
        "enqueue_connector_health_check_job",
        "enqueue_integration_sync_job",
        "recommend_safe_connector_task",
    }:
        from molecule_ranker.integrations import worker

        return getattr(worker, name)
    raise AttributeError(name)
