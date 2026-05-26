from __future__ import annotations

import json
import re
from typing import Any

FORBIDDEN_EXPERIMENTAL_OUTPUT_PATTERNS = [
    r"step-by-step protocol",
    r"exact lab procedure instructions",
    r"synthesis route",
    r"reagents list",
    r"reaction conditions",
    r"incubation time",
    r"temperature instructions",
    r"animal dosing",
    r"human dosing",
    r"patient treatment recommendation",
    r"proves efficacy",
    r"proves safety",
    r"\bcures\b",
    r"treats disease",
    r"\bmg/kg\b",
]

_OMIT_KEY_PATTERNS = [
    r"protocol",
    r"procedure",
    r"reagent",
    r"reaction",
    r"synthesis",
    r"temperature",
    r"incubat",
    r"dosing",
    r"dosage",
    r"dose",
]

_REPLACEMENTS = [
    (r"step-by-step protocol", "operational detail omitted"),
    (r"exact lab procedure instructions", "operational detail omitted"),
    (r"synthesis route", "chemistry execution detail omitted"),
    (r"reagents list", "materials detail omitted"),
    (r"\breagents?\b", "materials detail"),
    (r"reaction conditions", "chemistry execution detail omitted"),
    (r"incubation time", "timing detail omitted"),
    (r"\bincubat\w*\b", "timing detail omitted"),
    (r"temperature instructions", "condition detail omitted"),
    (r"\btemperature\b", "condition detail"),
    (r"animal dosing", "clinical-use detail omitted"),
    (r"human dosing", "clinical-use detail omitted"),
    (r"\bdosing\b", "clinical-use detail omitted"),
    (r"\bdosage\b", "clinical-use detail omitted"),
    (r"\bdose\b", "clinical-use detail omitted"),
    (r"\bmg/kg\b", "clinical-use unit omitted"),
    (r"patient treatment recommendation", "clinical recommendation omitted"),
    (r"proves efficacy", "requires cautious non-clinical interpretation"),
    (r"prove efficacy", "requires cautious non-clinical interpretation"),
    (r"proves safety", "requires independent safety interpretation"),
    (r"prove safety", "requires independent safety interpretation"),
    (r"\bcures\b", "does not establish disease benefit"),
    (r"treats disease", "has disease-context interpretation limits"),
    (r"\blab protocols?\b", "operational wet-lab details"),
    (r"\bprotocols?\b", "operational details"),
]


def sanitize_experimental_output_text(value: str) -> str:
    sanitized = value
    for pattern, replacement in _REPLACEMENTS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def sanitize_experimental_output_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_experimental_output_payload(item)
            for key, item in value.items()
            if not should_omit_experimental_output_key(str(key))
        }
    if isinstance(value, list):
        return [sanitize_experimental_output_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_experimental_output_text(value)
    return value


def should_omit_experimental_output_key(key: str) -> bool:
    normalized = re.sub(r"[_\\/\-]+", " ", key.lower())
    return any(re.search(pattern, normalized) for pattern in _OMIT_KEY_PATTERNS)


def validate_experimental_output_guardrails(value: Any, *, label: str = "output") -> None:
    serialized = json.dumps(value, default=str).lower()
    offenders = [
        pattern
        for pattern in FORBIDDEN_EXPERIMENTAL_OUTPUT_PATTERNS
        if re.search(pattern, serialized)
    ]
    if offenders:
        raise ValueError(
            f"Experimental {label} contains forbidden safety/integrity pattern(s): "
            f"{', '.join(offenders)}"
        )


__all__ = [
    "FORBIDDEN_EXPERIMENTAL_OUTPUT_PATTERNS",
    "sanitize_experimental_output_payload",
    "sanitize_experimental_output_text",
    "should_omit_experimental_output_key",
    "validate_experimental_output_guardrails",
]
