from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import EndToEndWorkflowRunner, WorkflowRunRequest
from molecule_ranker.v3 import (
    V3ProductContract,
    get_v3_product_contract,
    validate_v3_workflow_request,
)


def test_v3_product_contract_is_machine_readable_and_declares_defaults() -> None:
    contract = get_v3_product_contract()
    payload = contract.model_dump(mode="json")

    assert V3ProductContract.model_validate_json(json.dumps(payload)) == contract
    assert payload["product_name"] == "molecule-ranker"
    assert payload["product_version"] == "3.0.0"
    assert payload["product_positioning"] == (
        "autonomous discovery operating system for internal research planning"
    )
    assert payload["default_workflow"] == "full_discovery_loop"
    assert payload["default_mode"] == "dry_run"
    assert payload["default_generation_enabled"] is False
    assert payload["default_antibody_generation_enabled"] is False
    assert payload["default_external_writes_enabled"] is False
    assert payload["default_codex_autonomy"] == "execute_with_approval"


def test_v3_product_contract_forbids_unsafe_defaults() -> None:
    contract = get_v3_product_contract()
    text = " ".join([*contract.forbidden_outputs, *contract.limitations]).lower()

    assert contract.default_mode == "dry_run"
    assert contract.default_generation_enabled is False
    assert contract.default_antibody_generation_enabled is False
    assert contract.default_external_writes_enabled is False
    assert "no medical advice" in text
    assert "no dosing guidance" in text
    assert "no synthesis instructions" in text
    assert "no fabricated evidence" in text
    assert "no generated-molecule or generated-antibody claims" in text


def test_v3_workflow_request_validates_against_contract() -> None:
    valid = WorkflowRunRequest(
        workflow_type="full_discovery_loop",
        mode="dry_run",
        autonomy_level="execute_with_approval",
    )

    assert validate_v3_workflow_request(valid).valid is True

    invalid_workflow = WorkflowRunRequest(
        workflow_type="full_discovery_loop_with_biologics",
        mode="dry_run",
    )
    invalid = validate_v3_workflow_request(invalid_workflow)
    assert invalid.valid is False
    assert any("unsupported workflow" in issue for issue in invalid.issues)


def test_v3_result_bundle_includes_product_contract() -> None:
    result = EndToEndWorkflowRunner().run(
        WorkflowRunRequest(workflow_type="full_discovery_loop", mode="dry_run")
    )

    assert result.bundle is not None
    contract_payload = result.bundle.metadata["v3_product_contract"]
    assert contract_payload["product_version"] == "3.0.0"
    assert contract_payload["default_external_writes_enabled"] is False
    validation = EndToEndWorkflowValidator().validate_run_result(result)
    assert validation.passed is True
    assert validation.metadata["checks"]["v3_product_contract_valid"] is True


def test_v3_product_contract_rejects_unsafe_default_model_values() -> None:
    payload = get_v3_product_contract().model_dump(mode="python")
    payload["default_generation_enabled"] = True

    with pytest.raises(ValidationError, match="default_generation_enabled"):
        V3ProductContract.model_validate(payload)
