from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.integrations.connectors import BenchlingConnector
from molecule_ranker.integrations.connectors.base import ConnectorCallRecorder, ConnectorError
from molecule_ranker.integrations.schemas import ConnectorConfig, IntegrationCredentialRef


def test_benchling_health_check_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    client = FakeBenchlingClient(
        {
            ("GET", "https://benchling.example/api/v2/users/me"): {"id": "user-1"},
        }
    )
    connector = BenchlingConnector(_config(), http_client=client)

    health = connector.health_check()

    assert health.status == "ok"
    assert client.requests[-1]["headers"]["Authorization"] == "Bearer benchling-secret-value"
    assert "benchling-secret-value" not in health.model_dump_json()


def test_benchling_registry_search_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    connector = BenchlingConnector(
        _config(),
        http_client=FakeBenchlingClient(
            {
                ("GET", "https://benchling.example/api/v2/custom-entities"): {
                    "customEntities": [_entity("ent-1", inchi_key="AAAA", name="Rasagiline")]
                },
            }
        ),
    )

    results = connector.search_molecule(inchi_key="AAAA")

    assert results[0]["external_ref"].external_record_id == "ent-1"
    assert results[0]["inchi_key"] == "AAAA"
    assert results[0]["external_ref"].external_url == "https://benchling.example/ent-1"


def test_candidate_mapping_by_inchikey(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    recorder = ConnectorCallRecorder()
    connector = BenchlingConnector(
        _config(),
        recorder=recorder,
        http_client=FakeBenchlingClient(
            {
                ("GET", "https://benchling.example/api/v2/custom-entities"): {
                    "customEntities": [_entity("ent-1", inchi_key="MATCH", name="Candidate")]
                },
            }
        ),
    )

    result = connector.map_candidate({"candidate_id": "cand-1", "inchi_key": "MATCH"})

    mapping = result["mapping"]
    assert mapping.internal_entity_id == "cand-1"
    assert mapping.mapping_method == "inchi_key"
    assert mapping.status == "active"
    assert mapping.external_ref.external_record_id == "ent-1"
    assert recorder.sync_records[-1].action == "mapped"


def test_ambiguous_mapping_goes_pending_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    connector = BenchlingConnector(
        _config(),
        http_client=FakeBenchlingClient(
            {
                ("GET", "https://benchling.example/api/v2/custom-entities"): {
                    "customEntities": [
                        _entity("ent-1", inchi_key="DUP", name="A"),
                        _entity("ent-2", inchi_key="DUP", name="B"),
                    ]
                },
            }
        ),
    )

    result = connector.map_candidate({"candidate_id": "cand-1", "inchi_key": "DUP"})

    mapping = result["mapping"]
    assert mapping.status == "pending_review"
    assert mapping.mapping_confidence == 0.5
    assert mapping.metadata["ambiguity"] == "multiple_exact_matches"


def test_write_attempt_in_read_only_mode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    connector = BenchlingConnector(_config(), http_client=FakeBenchlingClient({}))

    with pytest.raises(ConnectorError, match="blocked by default"):
        connector.create_notebook_entry({"title": "Dossier", "summary": "Read-only attempt"})


def test_write_enabled_export_creates_sync_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHLING_API_KEY", "benchling-secret-value")
    recorder = ConnectorCallRecorder()
    connector = BenchlingConnector(
        _config(mode="write_enabled", allow_writes=True, explicit_write_permission=True),
        recorder=recorder,
        http_client=FakeBenchlingClient(
            {
                ("POST", "https://benchling.example/api/v2/entries"): {
                    "id": "entry-1",
                    "webURL": "https://benchling.example/entry-1",
                }
            }
        ),
    )

    result = connector.create_notebook_entry({"title": "Dossier", "summary": "Reviewed"})

    assert result["external_ref"].external_record_id == "entry-1"
    assert recorder.sync_records[-1].action == "exported"
    assert recorder.sync_records[-1].external_ref.external_record_type == "notebook_entry"


def test_missing_credentials_fail_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHLING_API_KEY", raising=False)
    connector = BenchlingConnector(_config(), http_client=FakeBenchlingClient({}))

    health = connector.health_check()
    with pytest.raises(ConnectorError, match="BENCHLING_API_KEY is not set"):
        connector.search_molecule("rasagiline")

    assert health.status == "unconfigured"


class FakeBenchlingClient:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
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


def _config(
    *,
    mode: str = "read_only",
    allow_writes: bool = False,
    explicit_write_permission: bool = False,
) -> ConnectorConfig:
    return ConnectorConfig(
        connector_id="benchling-test",
        name="Benchling",
        provider="benchling",
        kind="eln_lims",
        mode=mode,  # type: ignore[arg-type]
        base_url="https://benchling.example",
        credential_ref=IntegrationCredentialRef(
            credential_id="cred-benchling",
            backend="env",
            key_ref="BENCHLING_API_KEY",
        ),
        config={
            "benchling_registry_schema_id": "schema-registry",
            "benchling_notebook_folder_id": "folder-1",
            "benchling_registry_field_mapping": {
                "external_registry_id": ["registryId"],
                "inchi_key": ["fields.inchi_key"],
                "canonical_smiles": ["fields.canonical_smiles"],
                "name": ["name"],
            },
        },
        allow_writes=allow_writes,
        explicit_write_permission=explicit_write_permission,
    )


def _entity(entity_id: str, *, inchi_key: str, name: str) -> dict[str, Any]:
    return {
        "id": entity_id,
        "name": name,
        "registryId": f"REG-{entity_id}",
        "webURL": f"https://benchling.example/{entity_id}",
        "schemaId": "schema-registry",
        "fields": {
            "inchi_key": {"value": inchi_key},
            "canonical_smiles": {"value": "CCO"},
        },
    }
