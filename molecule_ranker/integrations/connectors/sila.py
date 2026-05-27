from __future__ import annotations

from typing import Any

from molecule_ranker.integrations.connectors.base import BaseConnector, ConnectorError


class SiLAMetadataAdapter(BaseConnector):
    provider = "sila_metadata"
    capabilities = ("metadata_adapter_placeholder",)
    limitations = BaseConnector.limitations + (
        "SiLA support is metadata-only; instrument control is explicitly out of scope.",
    )

    def export_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        raise ConnectorError("SiLA adapter does not control instruments or write device commands.")
