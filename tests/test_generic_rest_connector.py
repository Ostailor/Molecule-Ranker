from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.integrations.connectors import GenericRESTConnector
from molecule_ranker.integrations.connectors.base import ConnectorError
from molecule_ranker.integrations.schemas import ConnectorConfig, IntegrationCredentialRef


def test_generic_rest_get_list_records_mocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERIC_REST_TOKEN", "generic-secret-token")
    client = FakeRESTClient(
        {
            ("GET", "https://lims.example/api/results"): {
                "items": [
                    {
                        "id": "res-1",
                        "candidate_id": "cand-1",
                        "outcome_label": "positive",
                        "value": 1.2,
                        "unit": "nM",
                    }
                ]
            }
        }
    )
    connector = GenericRESTConnector(_config(tmp_path), http_client=client)

    records = connector.list_records(object_type="assay_result")

    assert records[0]["external_ref"].external_record_id == "res-1"
    assert records[0]["external_ref"].external_record_type == "assay_result"
    assert records[0]["payload"]["source_record_id"] == "res-1"
    assert Path(records[0]["raw_payload_artifact"]["path"]).exists()
    assert client.requests[-1]["headers"]["Authorization"] == "Bearer generic-secret-token"


def test_generic_rest_cursor_pagination_mocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERIC_REST_TOKEN", "generic-secret-token")
    client = FakeRESTClient(
        {
            ("GET", "https://lims.example/api/results?cursor=start"): {
                "items": [
                    {
                        "id": "res-1",
                        "candidate_id": "cand-1",
                        "outcome_label": "positive",
                        "value": 1.2,
                        "unit": "nM",
                    }
                ],
                "next_cursor": "next",
            },
            ("GET", "https://lims.example/api/results?cursor=next"): {
                "items": [
                    {
                        "id": "res-2",
                        "candidate_id": "cand-2",
                        "outcome_label": "negative",
                        "value": 2.1,
                        "unit": "nM",
                    }
                ]
            },
        }
    )
    connector = GenericRESTConnector(_config(tmp_path), http_client=client)

    records = connector.list_records(
        object_type="assay_result",
        params={"cursor": "start"},
    )

    assert [record["external_ref"].external_record_id for record in records] == ["res-1", "res-2"]


def test_generic_rest_post_blocked_in_read_only(tmp_path: Path) -> None:
    connector = GenericRESTConnector(_config(tmp_path), http_client=FakeRESTClient({}))

    with pytest.raises(ConnectorError, match="blocked by default"):
        connector.export_record({"id": "res-export", "candidate_id": "cand-1"})


def test_generic_rest_data_contract_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERIC_REST_TOKEN", "generic-secret-token")
    connector = GenericRESTConnector(
        _config(tmp_path),
        http_client=FakeRESTClient(
            {
                ("GET", "https://lims.example/api/results"): {
                    "items": [
                        {
                            "id": "res-1",
                            "candidate_id": "cand-1",
                            "outcome_label": "invalid",
                            "value": 1.2,
                            "unit": "nM",
                        }
                    ]
                }
            }
        ),
    )

    with pytest.raises(ConnectorError, match="data contract validation"):
        connector.list_records(object_type="assay_result")


def test_generic_rest_auth_redaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERIC_REST_TOKEN", "generic-secret-token")
    connector = GenericRESTConnector(_config(tmp_path), http_client=FakeRESTClient({}))

    headers = connector.redacted_headers()
    payload = json.dumps(headers, sort_keys=True)

    assert headers["Authorization"] == "[REDACTED]"
    assert "generic-secret-token" not in payload


class FakeRESTClient:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        params = kwargs.get("params") or {}
        if params:
            query = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
            url = f"{url}?{query}"
        self.requests.append({"method": method, "url": url, **kwargs})
        key = (method, url)
        if key not in self.routes:
            return FakeResponse({"message": "not found"}, status_code=404)
        return FakeResponse(self.routes[key])


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ConnectorError(f"HTTP {self.status_code}")


def _config(tmp_path: Path) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="generic-rest-test",
        name="Generic REST",
        provider="generic_rest",
        kind="generic_rest",
        mode="read_only",
        base_url="https://lims.example",
        credential_ref=IntegrationCredentialRef(
            credential_id="cred-generic",
            backend="env",
            key_ref="GENERIC_REST_TOKEN",
        ),
        config={
            "endpoints": {
                "list_records": "/api/results",
                "get_record": "/api/results/{record_id}",
                "export_record": "/api/results",
            },
            "pagination": {
                "strategy": "cursor",
                "cursor_param": "cursor",
                "next_cursor_path": "next_cursor",
            },
            "response_paths": {
                "records": "items",
                "record_id": ["id"],
            },
            "artifact_dir": str(tmp_path / "artifacts"),
            "data_contract": {
                "contract_id": "assay-result-v1",
                "name": "Assay result",
                "object_type": "assay_result",
                "version": "1",
                "required_fields": ["candidate_id", "outcome_label", "value", "unit"],
                "optional_fields": [],
                "field_types": {
                    "candidate_id": "string",
                    "source_record_id": "string",
                    "outcome_label": "string",
                    "value": "number",
                    "unit": "string",
                },
                "controlled_vocabularies": {
                    "outcome_label": ["positive", "negative", "inconclusive", "failed_qc"]
                },
                "identifier_fields": ["candidate_id"],
                "validation_rules": [],
                "metadata": {},
            },
        },
    )
