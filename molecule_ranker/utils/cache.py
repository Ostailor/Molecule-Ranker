from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonCache:
    """Small JSON-file cache for real public adapter responses."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def make_key(self, namespace: str, request: dict[str, Any]) -> str:
        normalized = json.dumps(request, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        safe_namespace = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in namespace)
        return f"{safe_namespace}-{digest}"

    def get(self, key: str, *, ttl_seconds: int | None = None) -> dict[str, Any] | None:
        entry = self.get_entry(key)
        if entry is None:
            return None
        if self._is_expired(entry, ttl_seconds=ttl_seconds):
            return None
        payload = entry.get("payload")
        return payload if isinstance(payload, dict) else None

    def get_entry(self, key: str) -> dict[str, Any] | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        return payload if isinstance(payload, dict) else None

    def set(
        self,
        key: str,
        value: dict[str, Any],
        *,
        source: str,
        endpoint: str,
        request: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        path = self.cache_dir / f"{key}.json"
        entry = {
            "payload": value,
            "cached_at": datetime.now(UTC).isoformat(),
            "ttl_seconds": ttl_seconds,
            "provenance": {
                "source": source,
                "endpoint": endpoint,
                "request": request,
            },
        }
        path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")

    def _is_expired(self, entry: dict[str, Any], *, ttl_seconds: int | None) -> bool:
        ttl = ttl_seconds if ttl_seconds is not None else entry.get("ttl_seconds")
        if ttl is None:
            return False
        if ttl <= 0:
            return True
        cached_at = entry.get("cached_at")
        if not isinstance(cached_at, str):
            return True
        try:
            cached_datetime = datetime.fromisoformat(cached_at)
        except ValueError:
            return True
        age = datetime.now(UTC) - cached_datetime
        return age.total_seconds() > ttl
