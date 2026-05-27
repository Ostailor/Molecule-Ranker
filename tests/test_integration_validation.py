from __future__ import annotations

from pathlib import Path

from molecule_ranker.integrations.schemas import DataContract
from molecule_ranker.integrations.validation import (
    export_contract,
    import_contract,
    infer_contract_from_sample,
    normalize_record,
    validate_record_against_contract,
)


def test_valid_assay_result_passes() -> None:
    contract = _assay_contract()
    record = {
        "candidate_id": "cand-1",
        "source_record_id": "benchling-result-1",
        "outcome_label": "positive",
        "measured_value": 12.4,
        "unit": "nM",
        "measured_at": "2026-05-27T12:00:00+00:00",
    }

    assert validate_record_against_contract(record, contract) == []
    assert normalize_record(record, contract)["unit"] == "nm"


def test_missing_candidate_id_fails() -> None:
    issues = validate_record_against_contract(
        {
            "source_record_id": "benchling-result-1",
            "outcome_label": "positive",
            "measured_value": 12.4,
            "unit": "nM",
        },
        _assay_contract(),
    )

    assert "candidate_id: required field is missing" in issues


def test_invalid_outcome_label_fails() -> None:
    issues = validate_record_against_contract(
        {
            "candidate_id": "cand-1",
            "source_record_id": "benchling-result-1",
            "outcome_label": "cured",
            "measured_value": 12.4,
            "unit": "nM",
        },
        _assay_contract(),
    )

    assert "outcome_label: value is outside controlled vocabulary" in issues


def test_forbidden_protocol_field_fails() -> None:
    issues = validate_record_against_contract(
        {
            "candidate_id": "cand-1",
            "source_record_id": "benchling-result-1",
            "outcome_label": "positive",
            "measured_value": 12.4,
            "unit": "nM",
            "protocol_steps": ["do not import lab procedure text"],
        },
        _assay_contract(),
    )

    assert "protocol_steps: forbidden protocol/synthesis/dosing field is present" in issues


def test_secret_looking_field_is_flagged() -> None:
    issues = validate_record_against_contract(
        {
            "candidate_id": "cand-1",
            "source_record_id": "benchling-result-1",
            "outcome_label": "positive",
            "measured_value": 12.4,
            "unit": "nM",
            "api_key": "sk-secretsecretsecretsecret",
        },
        _assay_contract(),
    )

    assert any("api_key" in issue and "secret-looking" in issue for issue in issues)


def test_contract_inference_and_import_export_roundtrip(tmp_path: Path) -> None:
    contract = infer_contract_from_sample(
        [
            {
                "candidate_id": "cand-1",
                "source_record_id": "src-1",
                "outcome_label": "positive",
                "measured_value": 1.0,
            },
            {
                "candidate_id": "cand-2",
                "source_record_id": "src-2",
                "outcome_label": "negative",
                "measured_value": 2.0,
            },
        ]
    )
    path = tmp_path / "contract.json"

    export_contract(contract, path)
    loaded = import_contract(path)

    assert loaded == contract
    assert loaded.field_types["measured_value"] == "number"
    assert loaded.controlled_vocabularies["outcome_label"] == ["negative", "positive"]


def _assay_contract() -> DataContract:
    return DataContract(
        contract_id="assay-result-v1",
        name="Assay result",
        object_type="assay_result",
        version="1.0",
        required_fields=["outcome_label", "measured_value", "unit"],
        optional_fields=["measured_at"],
        field_types={
            "candidate_id": "string",
            "source_record_id": "string",
            "outcome_label": "string",
            "measured_value": "number",
            "unit": "string",
            "measured_at": "datetime",
        },
        controlled_vocabularies={
            "outcome_label": ["positive", "negative", "inconclusive", "failed_qc"]
        },
        identifier_fields=["candidate_id"],
        validation_rules=[],
    )
