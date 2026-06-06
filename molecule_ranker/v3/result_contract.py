from __future__ import annotations

REQUIRED_RESULT_ARTIFACTS: list[str] = [
    "v3_product_contract",
    "workflow_summary",
    "result_bundle",
    "lineage_manifest",
    "validation_report",
    "guardrail_summary",
    "approval_summary",
    "audit_trail",
    "limitations",
]

__all__ = ["REQUIRED_RESULT_ARTIFACTS"]
