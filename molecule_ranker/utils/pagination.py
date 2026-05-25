from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class PaginationMetadata:
    pages_fetched: int = 0
    records_fetched: int = 0
    records_retained: int = 0
    truncated: bool = False

    def asdict(self) -> dict[str, Any]:
        return {
            "pages_fetched": self.pages_fetched,
            "records_fetched": self.records_fetched,
            "records_retained": self.records_retained,
            "truncated": self.truncated,
        }


@dataclass
class PaginatedResult:
    records: list[dict[str, Any]]
    metadata: PaginationMetadata
    page_payloads: list[dict[str, Any]]


def paginate_chembl_list(
    fetch_page: Callable[[int, int], dict[str, Any]],
    *,
    collection_key: str,
    max_records: int,
    page_size: int = 100,
) -> PaginatedResult:
    """Collect ChEMBL list payloads until exhausted or a caller limit is reached."""

    if max_records <= 0:
        return PaginatedResult(records=[], metadata=PaginationMetadata(), page_payloads=[])

    safe_page_size = max(1, min(page_size, max_records))
    offset = 0
    retained: list[dict[str, Any]] = []
    page_payloads: list[dict[str, Any]] = []
    metadata = PaginationMetadata()

    while len(retained) < max_records:
        payload = fetch_page(offset, safe_page_size)
        page_payloads.append(payload)
        metadata.pages_fetched += 1
        raw_records = payload.get(collection_key, []) or []
        if not isinstance(raw_records, list) or not raw_records:
            break

        metadata.records_fetched += len(raw_records)
        remaining = max_records - len(retained)
        page_records = [record for record in raw_records if isinstance(record, dict)]
        retained.extend(page_records[:remaining])

        if len(page_records) > remaining:
            metadata.truncated = True
            break

        if not _has_next_page(payload):
            break

        offset += safe_page_size

    metadata.records_retained = len(retained)
    if len(retained) >= max_records and _has_next_page(page_payloads[-1]):
        metadata.truncated = True
    return PaginatedResult(records=retained, metadata=metadata, page_payloads=page_payloads)


def _has_next_page(payload: dict[str, Any]) -> bool:
    page_meta = payload.get("page_meta")
    return isinstance(page_meta, dict) and bool(page_meta.get("next"))
