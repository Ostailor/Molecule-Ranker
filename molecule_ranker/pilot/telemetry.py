from __future__ import annotations

from typing import Any

from molecule_ranker.platform.observability import metrics


def pilot_telemetry_snapshot() -> dict[str, Any]:
    snapshot = metrics.snapshot()
    return {
        "metrics": snapshot,
        "contains_secret_values": False,
        "contains_biomedical_evidence": False,
    }


__all__ = ["pilot_telemetry_snapshot"]
