from __future__ import annotations

import pytest
import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.data_sources.opentargets_adapter import OpenTargetsAdapter
from molecule_ranker.schemas import Disease
from molecule_ranker.utils.http_cache import HttpResponseCache


class MockResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class QueueSession:
    def __init__(self, responses: list[MockResponse] | None = None, error: Exception | None = None):
        self.responses = responses or []
        self.error = error
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0)


def test_http_cache_key_changes_with_graphql_variables(tmp_path):
    cache = HttpResponseCache(tmp_path)

    key_a = cache.build_key(
        source_name="Open Targets",
        endpoint="https://api.platform.opentargets.org/api/v4/graphql",
        method="POST",
        graphql_variables={"efoId": "MONDO_1"},
        request_body={"query": "query Test { disease { id } }"},
    )
    key_b = cache.build_key(
        source_name="Open Targets",
        endpoint="https://api.platform.opentargets.org/api/v4/graphql",
        method="POST",
        graphql_variables={"efoId": "MONDO_2"},
        request_body={"query": "query Test { disease { id } }"},
    )

    assert key_a != key_b


def test_http_cache_writes_only_successful_real_responses(tmp_path):
    cache = HttpResponseCache(tmp_path)
    session = QueueSession([MockResponse({"data": {"__typename": "Query"}})])
    adapter = OpenTargetsAdapter(session=session, cache=cache)  # type: ignore[arg-type]

    payload = adapter._graphql("query HealthCheck { __typename }", {})

    assert payload == {"data": {"__typename": "Query"}}
    assert len(list(tmp_path.glob("*.json"))) == 1
    entry = cache.get_entry(next(tmp_path.glob("*.json")).stem)
    assert entry is not None
    assert entry.response_json == payload
    assert entry.source == "Open Targets"
    assert entry.request_metadata["method"] == "POST"


def test_http_cache_does_not_write_failed_responses(tmp_path):
    cache = HttpResponseCache(tmp_path)
    session = QueueSession(error=requests.Timeout("timeout"))
    adapter = OpenTargetsAdapter(session=session, cache=cache)  # type: ignore[arg-type]

    with pytest.raises(ExternalDataUnavailableError):
        adapter._graphql("query HealthCheck { __typename }", {})

    assert list(tmp_path.glob("*.json")) == []


def test_live_failure_uses_cached_real_response_only_when_enabled(tmp_path):
    cache = HttpResponseCache(tmp_path)
    success_session = QueueSession(
        [
            MockResponse(
                {
                    "data": {
                        "disease": {
                            "associatedTargets": {
                                "rows": [
                                    {
                                        "score": 0.88,
                                        "target": {
                                            "id": "ENSG1",
                                            "approvedSymbol": "LRRK2",
                                            "approvedName": "LRRK2 kinase",
                                            "biotype": "protein_coding",
                                            "proteinIds": [],
                                            "tractability": [],
                                            "safetyLiabilities": [],
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            )
        ]
    )
    disease = Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"open_targets": "MONDO_0005180"},
    )
    OpenTargetsAdapter(session=success_session, cache=cache).discover_targets(disease, limit=1)  # type: ignore[arg-type]

    failing_session = QueueSession(error=requests.Timeout("offline"))
    no_cache_adapter = OpenTargetsAdapter(
        session=failing_session,  # type: ignore[arg-type]
        cache=cache,
        use_cache=False,
    )
    with pytest.raises(ExternalDataUnavailableError):
        no_cache_adapter.discover_targets(disease, limit=1)

    cached_adapter = OpenTargetsAdapter(
        session=QueueSession(error=requests.Timeout("offline")),  # type: ignore[arg-type]
        cache=cache,
        use_cache=True,
    )
    targets = cached_adapter.discover_targets(disease, limit=1)

    assert targets[0].symbol == "LRRK2"
    provenance = targets[0].evidence[0].metadata["response_provenance"]
    assert provenance["mode"] == "cached-real-data"
    assert provenance["source"] == "Open Targets"
    assert provenance["cache_key"]
    assert provenance["retrieved_at"]
