from __future__ import annotations

import requests

from molecule_ranker.utils.pagination import paginate_chembl_list
from molecule_ranker.utils.retry import RetryPolicy, request_with_retries


class MockResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


def test_retry_retries_429_with_exponential_backoff_and_jitter():
    responses = [
        MockResponse({}, status_code=429),
        MockResponse({"ok": True}),
    ]
    sleeps: list[float] = []

    response, metadata = request_with_retries(
        lambda: responses.pop(0),
        RetryPolicy(max_retries=1, backoff_seconds=0.25, jitter_seconds=0.1),
        sleep=sleeps.append,
        jitter=lambda _upper: 0.05,
    )

    assert response.json() == {"ok": True}
    assert metadata.retry_count == 1
    assert metadata.rate_limit_retry_count == 1
    assert metadata.status_codes == [429, 200]
    assert sleeps == [0.3]


def test_retry_retries_500_but_not_400():
    server_responses = [
        MockResponse({}, status_code=500),
        MockResponse({"ok": True}),
    ]
    response, metadata = request_with_retries(
        lambda: server_responses.pop(0),
        RetryPolicy(max_retries=1, backoff_seconds=0, jitter_seconds=0),
        sleep=lambda _seconds: None,
        jitter=lambda _upper: 0,
    )

    assert response.json() == {"ok": True}
    assert metadata.retry_count == 1
    assert metadata.status_codes == [500, 200]

    bad_request_calls = 0

    def bad_request() -> MockResponse:
        nonlocal bad_request_calls
        bad_request_calls += 1
        return MockResponse({}, status_code=400)

    try:
        request_with_retries(
            bad_request,
            RetryPolicy(max_retries=3, backoff_seconds=0, jitter_seconds=0),
            sleep=lambda _seconds: None,
            jitter=lambda _upper: 0,
        )
    except requests.HTTPError:
        pass

    assert bad_request_calls == 1


def test_paginated_chembl_responses_are_combined_and_truncation_is_recorded():
    pages = [
        {
            "activities": [{"activity_id": "A1"}, {"activity_id": "A2"}],
            "page_meta": {"next": "activity.json?offset=2"},
        },
        {
            "activities": [{"activity_id": "A3"}, {"activity_id": "A4"}],
            "page_meta": {"next": "activity.json?offset=4"},
        },
    ]
    offsets: list[int] = []

    def fetch_page(offset: int, page_size: int) -> dict:
        offsets.append(offset)
        return pages.pop(0)

    result = paginate_chembl_list(
        fetch_page,
        collection_key="activities",
        max_records=3,
        page_size=2,
    )

    assert [row["activity_id"] for row in result.records] == ["A1", "A2", "A3"]
    assert offsets == [0, 2]
    assert result.metadata.pages_fetched == 2
    assert result.metadata.records_fetched == 4
    assert result.metadata.records_retained == 3
    assert result.metadata.truncated is True
