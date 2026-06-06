from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.v3.product_contract import (
    V3ProductContract,
    get_v3_product_contract,
)


class V3WorkflowContractValidation(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)
    contract_version: str
    workflow_type: str | None = None
    mode: str | None = None


def validate_v3_workflow_request(
    request: Any,
    *,
    contract: V3ProductContract | None = None,
) -> V3WorkflowContractValidation:
    active_contract = contract or get_v3_product_contract()
    workflow_type = _get_value(request, "workflow_type")
    mode = _get_value(request, "mode")
    issues: list[str] = []

    if workflow_type not in active_contract.supported_workflows:
        issues.append(f"unsupported workflow for V3 product contract: {workflow_type}")
    if mode not in active_contract.supported_modes:
        issues.append(f"unsupported mode for V3 product contract: {mode}")
    if _get_value(request, "antibody_generation_enabled") is True:
        plugin_ids = _get_value(request, "approved_antibody_generation_plugin_ids") or []
        if not plugin_ids:
            issues.append("antibody generation requires approved plugin ids")
    if _get_value(request, "requested_external_write") is True and mode != "write_approved_live":
        issues.append("external writes require write_approved_live mode and approval")

    return V3WorkflowContractValidation(
        valid=not issues,
        issues=issues,
        contract_version=active_contract.product_contract_version,
        workflow_type=str(workflow_type) if workflow_type is not None else None,
        mode=str(mode) if mode is not None else None,
    )


def validate_v3_workflow(
    workflow: Any,
    *,
    contract: V3ProductContract | None = None,
) -> V3WorkflowContractValidation:
    return validate_v3_workflow_request(workflow, contract=contract)


def _get_value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


__all__ = [
    "V3WorkflowContractValidation",
    "validate_v3_workflow",
    "validate_v3_workflow_request",
]
