from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def structure_oracle_context_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    for key in (
        "structure_oracle",
        "structure_aware_assessment",
        "structure_assessment",
        "structure_report_card",
    ):
        value = metadata.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    oracle = metadata.get("oracle_scoring")
    if isinstance(oracle, Mapping):
        value = oracle.get("structure_oracle")
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def structure_oracle_domain_multiplier(applicability_domain: str) -> float:
    if applicability_domain == "suitable_experimental_structure":
        return 1.0
    if applicability_domain == "lower_confidence_predicted_structure":
        return 0.72
    if applicability_domain == "weak_or_unknown_structure":
        return 0.55
    if applicability_domain == "unavailable":
        return 1.0
    return 0.75


def structure_oracle_metadata(
    context: Mapping[str, Any],
    method: str,
) -> dict[str, Any]:
    return {
        "available": context.get("applicability_domain") != "unavailable",
        "method": method,
        "applicability_domain": context.get("applicability_domain", "unknown"),
        "not_experimental_evidence": True,
        "not_activity_evidence": True,
        "does_not_validate_generated_molecule": True,
        "cannot_override_experimental_negative_or_safety_results": True,
    }


__all__ = [
    "structure_oracle_context_from_metadata",
    "structure_oracle_domain_multiplier",
    "structure_oracle_metadata",
]
