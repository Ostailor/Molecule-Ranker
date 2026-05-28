from __future__ import annotations

from typing import Any

DEFAULT_ALLOWED_ELEMENTS = ["C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"]
DEFAULT_FORBIDDEN_PATTERNS = [
    "critical_toxicity_alert",
    "unsupported_synthesis_claim",
    "validated_activity_claim",
    "direct_safety_claim",
]


def default_hard_constraints(
    *,
    allowed_elements: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "valid_molecule": True,
        "allowed_elements": list(allowed_elements or DEFAULT_ALLOWED_ELEMENTS),
        "no_critical_alerts": True,
        "duplicate_rejection": True,
        "generated_label": "generated",
    }


def default_soft_constraints(
    *,
    target_relevance: float,
    seed_similarity_range: tuple[float, float] = (0.35, 0.85),
) -> dict[str, Any]:
    return {
        "target_relevance": {
            "goal": "prefer_higher_source_backed_relevance",
            "value": round(max(0.0, min(float(target_relevance), 1.0)), 3),
        },
        "seed_similarity_range": {
            "min": seed_similarity_range[0],
            "max": seed_similarity_range[1],
            "goal": "retain target-conditioned analog relationship without duplicates",
        },
        "novelty": {"goal": "prefer_non_duplicate_novel_analogs"},
        "developability": {"goal": "prefer lower deterministic triage risk"},
        "experimental_gap": {"goal": "prioritize unresolved review questions"},
        "literature_context": {"goal": "use parent seed or target context only"},
        "diversity": {"goal": "avoid overloading one scaffold cluster"},
    }


def default_optimization_goals() -> list[dict[str, Any]]:
    return [
        {"name": "chemical_validity", "direction": "maximize", "source": "deterministic"},
        {"name": "target_conditioning", "direction": "maximize", "source": "seed_context"},
        {"name": "novelty", "direction": "maximize", "source": "similarity_filter"},
        {"name": "diversity", "direction": "maximize", "source": "diversity_filter"},
        {"name": "developability", "direction": "maximize", "source": "triage_modules"},
    ]
