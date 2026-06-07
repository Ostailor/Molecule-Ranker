from __future__ import annotations

from typing import Any

PAYMENTS_IMPLEMENTED = False
PRODUCTION_BILLING_ENABLED = False

PILOT_PRICING_MODEL: dict[str, dict[str, Any]] = {
    "free_internal": {
        "billing_status": "not_billable",
        "description": "Internal validation and productization use only.",
    },
    "pilot": {
        "billing_status": "pilot_terms_pending",
        "description": "Paid pilot packaging placeholder; no payment processing is implemented.",
    },
    "admin": {
        "billing_status": "not_billable",
        "description": "Administrative access for product and support operators.",
    },
}


def pricing_model_payload() -> dict[str, Any]:
    return {
        "payments_implemented": PAYMENTS_IMPLEMENTED,
        "production_billing_enabled": PRODUCTION_BILLING_ENABLED,
        "plans": PILOT_PRICING_MODEL,
    }


__all__ = [
    "PAYMENTS_IMPLEMENTED",
    "PILOT_PRICING_MODEL",
    "PRODUCTION_BILLING_ENABLED",
    "pricing_model_payload",
]
