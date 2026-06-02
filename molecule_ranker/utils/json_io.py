from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class JsonArtifactTooLargeError(ValueError):
    pass


def load_json_file(
    path: str | Path,
    *,
    max_bytes: int | None = 25_000_000,
) -> Any:
    target = Path(path)
    if max_bytes is not None and target.stat().st_size > max_bytes:
        raise JsonArtifactTooLargeError(
            f"JSON artifact exceeds bounded-memory limit: {target}"
        )
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_json_array(
    path: str | Path,
    *,
    chunk_size: int = 1_048_576,
) -> Iterator[Any]:
    """Stream values from a top-level JSON array without loading the full file."""
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    with Path(path).open("r", encoding="utf-8") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk and not buffer:
                break
            buffer += chunk
            while True:
                stripped = buffer.lstrip()
                if not started:
                    if not stripped:
                        break
                    if not stripped.startswith("["):
                        raise ValueError("iter_json_array requires a top-level JSON array.")
                    buffer = stripped[1:]
                    started = True
                    continue
                stripped = buffer.lstrip()
                if not stripped:
                    buffer = stripped
                    break
                if stripped.startswith("]"):
                    return
                if stripped.startswith(","):
                    buffer = stripped[1:]
                    continue
                try:
                    value, index = decoder.raw_decode(stripped)
                except json.JSONDecodeError:
                    if not chunk:
                        raise
                    buffer = stripped
                    break
                yield value
                buffer = stripped[index:]
            if not chunk:
                break


__all__ = ["JsonArtifactTooLargeError", "iter_json_array", "load_json_file"]
