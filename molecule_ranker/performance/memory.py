from __future__ import annotations

import tracemalloc
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def measure_memory_for_step(
    step_name: str,
    callback: Callable[[], T],
) -> tuple[T, dict[str, int | str]]:
    """Run a local callback and return lightweight tracemalloc usage."""
    tracemalloc.start()
    try:
        result = callback()
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, {
        "step": step_name,
        "current_bytes": int(current_bytes),
        "peak_bytes": int(peak_bytes),
    }


__all__ = ["measure_memory_for_step"]
