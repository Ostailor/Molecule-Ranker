from __future__ import annotations

from molecule_ranker.product.schemas import PilotOrganization, PilotPlan, PilotUser

PILOT_PLANS: tuple[PilotPlan, ...] = ("free_internal", "pilot", "admin")
PILOT_USER_STATUSES: tuple[str, ...] = ("invited", "active", "suspended", "cancelled")
PILOT_ORGANIZATION_STATUSES: tuple[str, ...] = ("invited", "active", "suspended", "cancelled")


def build_invited_pilot_user(
    *,
    user_id: str,
    email: str,
    name: str | None = None,
    organization_name: str | None = None,
    role: str | None = None,
    plan: PilotPlan = "pilot",
) -> PilotUser:
    return PilotUser(
        user_id=user_id,
        email=email,
        name=name,
        organization_name=organization_name,
        role=role,
        plan=plan,
        status="invited",
    )


def build_pilot_organization(
    *,
    organization_id: str,
    name: str,
    owner_user_id: str,
    plan: str = "pilot",
) -> PilotOrganization:
    return PilotOrganization(
        organization_id=organization_id,
        name=name,
        owner_user_id=owner_user_id,
        plan=plan,
        status="invited",
    )


__all__ = [
    "PILOT_ORGANIZATION_STATUSES",
    "PILOT_PLANS",
    "PILOT_USER_STATUSES",
    "build_invited_pilot_user",
    "build_pilot_organization",
]
