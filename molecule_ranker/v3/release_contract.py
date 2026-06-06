from __future__ import annotations

from typing import Any

from molecule_ranker.v3.product_contract import (
    V3_PRODUCT_CONTRACT_VERSION,
    v3_product_contract_payload,
)

V3_RELEASE_CONTRACT_VERSION = "v3.0.0"


def v3_release_contract_payload() -> dict[str, Any]:
    return {
        "release_contract_version": V3_RELEASE_CONTRACT_VERSION,
        "product_contract_version": V3_PRODUCT_CONTRACT_VERSION,
        "product_contract": v3_product_contract_payload(),
        "validation_scope": "software_autonomy_validation_not_clinical_validation",
    }


__all__ = ["V3_RELEASE_CONTRACT_VERSION", "v3_release_contract_payload"]
