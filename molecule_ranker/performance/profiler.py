from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from molecule_ranker import __version__
from molecule_ranker.performance.benchmarks import PERFORMANCE_STEPS, synthetic_benchmark_steps
from molecule_ranker.performance.memory import measure_memory_for_step


class PerformanceProfile(BaseModel):
    profile_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: str
    workflow: str
    environment: str = "synthetic"
    live_apis_enabled: bool = False
    measurements: dict[str, dict[str, Any]]
    memory_usage_by_step: dict[str, dict[str, Any]]
    codex_task_metrics: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def require_timezone_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


def profile_synthetic_workflow(
    workflow: str = "golden",
    *,
    metadata: dict[str, Any] | None = None,
) -> PerformanceProfile:
    steps = synthetic_benchmark_steps(workflow)
    measurements: dict[str, dict[str, Any]] = {}
    memory_usage: dict[str, dict[str, Any]] = {}
    for step_name in PERFORMANCE_STEPS:
        callback = steps[step_name]
        started = time.perf_counter()
        result, memory = measure_memory_for_step(step_name, callback)
        duration_ms = (time.perf_counter() - started) * 1000
        measurements[step_name] = {
            "duration_ms": round(duration_ms, 3),
            "source": "synthetic",
            "result_summary": result,
        }
        memory_usage[step_name] = memory

    codex_result = measurements["codex_task"]["result_summary"]
    codex_task_metrics = {
        "duration_ms": measurements["codex_task"]["duration_ms"],
        "task_count": codex_result.get("task_count", 0),
        "timeout_count": codex_result.get("timeout_count", 0),
        "timeout_rate": _timeout_rate(codex_result),
        "source": "synthetic",
    }
    return PerformanceProfile(
        profile_id=f"perf-{uuid.uuid4().hex[:12]}",
        version=__version__,
        workflow=workflow,
        environment="synthetic",
        live_apis_enabled=False,
        measurements=measurements,
        memory_usage_by_step=memory_usage,
        codex_task_metrics=codex_task_metrics,
        metadata={
            "profile_mode": "synthetic_baseline",
            "live_api_policy": "disabled_by_default",
            **(metadata or {}),
        },
    )


def _timeout_rate(codex_result: dict[str, Any]) -> float:
    task_count = int(codex_result.get("task_count") or 0)
    if task_count <= 0:
        return 0.0
    timeout_count = int(codex_result.get("timeout_count") or 0)
    return round(timeout_count / task_count, 6)


__all__ = ["PerformanceProfile", "profile_synthetic_workflow"]
