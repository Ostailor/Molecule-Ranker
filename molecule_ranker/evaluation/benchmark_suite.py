from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.evaluation.schemas import BenchmarkSuite


def create_benchmark_suite(
    *,
    suite_id: str,
    name: str,
    version: str,
    description: str,
    tasks: list[str] | None = None,
) -> BenchmarkSuite:
    return BenchmarkSuite(
        suite_id=suite_id,
        name=name,
        version=version,
        description=description,
        tasks=list(tasks or []),
        created_at=datetime.now(UTC),
    )


__all__ = ["BenchmarkSuite", "create_benchmark_suite"]

