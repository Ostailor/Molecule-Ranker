from __future__ import annotations

PILOT_ONBOARDING_STEPS: tuple[str, ...] = (
    "confirm_research_use_only",
    "confirm_no_clinical_or_patient_use",
    "create_pilot_workspace",
    "invite_research_users",
    "acknowledge_guardrails",
    "configure_usage_limits",
    "run_sample_source_backed_ranking",
    "review_result_bundle_disclaimers",
)

PILOT_APPROVAL_REQUIREMENTS: tuple[str, ...] = (
    "organization_owner_identified",
    "research_use_case_recorded",
    "guardrail_acknowledgement_recorded",
    "admin_review_before_external_writes",
)


def onboarding_checklist() -> list[str]:
    return list(PILOT_ONBOARDING_STEPS)


__all__ = [
    "PILOT_APPROVAL_REQUIREMENTS",
    "PILOT_ONBOARDING_STEPS",
    "onboarding_checklist",
]
