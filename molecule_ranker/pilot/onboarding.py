from __future__ import annotations


def pilot_onboarding_checklist() -> list[str]:
    return [
        "Confirm V2.2.0 version and stable V1 contract identifiers.",
        "Confirm internal research use only boundaries.",
        "Confirm generated molecules remain computational hypotheses.",
        "Confirm Codex outputs are assistant artifacts, not evidence or decisions.",
        "Confirm support and feedback workflows do not collect secrets.",
    ]


__all__ = ["pilot_onboarding_checklist"]
