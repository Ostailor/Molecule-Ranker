from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.schemas import (
    DataContract,
    DataContractIssue,
    DataContractValidationReport,
    ExternalRecordEnvelope,
)

SUPPORTED_OBJECT_TYPES = {
    "molecule_candidate",
    "generated_molecule",
    "assay_result",
    "assay_run",
    "compound_registry_entry",
    "review_decision",
    "validation_handoff",
    "active_learning_batch",
}

OBJECT_IDENTIFIER_FIELDS = {
    "molecule_candidate": ["candidate_id"],
    "generated_molecule": ["candidate_id"],
    "assay_result": ["candidate_id"],
    "assay_run": ["assay_run_id"],
    "compound_registry_entry": ["registry_id"],
    "review_decision": ["review_item_id"],
    "validation_handoff": ["handoff_id"],
    "active_learning_batch": ["batch_id"],
}

FORBIDDEN_FIELD_MARKERS = {
    "device_command",
    "dose",
    "dosing",
    "instrument_command",
    "instrument_control",
    "lab_protocol",
    "patient_treatment",
    "protocol",
    "protocol_step",
    "protocol_steps",
    "synthesis",
    "synthesis_instruction",
    "synthesis_route",
    "treatment_guidance",
}

COMMON_UNIT_FIELDS = {
    "concentration_unit",
    "dose_unit",
    "measured_unit",
    "measurement_unit",
    "unit",
    "units",
    "value_unit",
}

PARSEABLE_UNITS = {
    "%",
    "au",
    "copies",
    "count",
    "counts",
    "day",
    "days",
    "fold",
    "hour",
    "hours",
    "m",
    "mg/ml",
    "mm",
    "ng/ml",
    "nm",
    "pm",
    "ratio",
    "rfu",
    "ug/ml",
    "um",
    "µm",
}


def validate_data_contract(
    rows: Iterable[dict[str, Any] | ExternalRecordEnvelope],
    contract: DataContract,
) -> DataContractValidationReport:
    materialized = list(rows)
    issues: list[DataContractIssue] = []
    for index, row in enumerate(materialized, start=1):
        for message in validate_record_against_contract(row, contract):
            field, issue = _split_issue(message)
            issues.append(DataContractIssue(row_index=index, field=field, issue=issue))
    return DataContractValidationReport(
        contract_id=contract.contract_id,
        contract_name=contract.name,
        valid=not issues,
        row_count=len(materialized),
        issue_count=len(issues),
        issues=issues,
    )


def validate_record_against_contract(
    record: dict[str, Any] | ExternalRecordEnvelope,
    contract: DataContract,
) -> list[str]:
    payload = _payload(record)
    issues: list[str] = []
    for field in _required_fields(contract):
        if not _has_value(payload.get(field)):
            issues.append(f"{field}: required field is missing")
    for field in contract.identifier_fields:
        if not _has_value(payload.get(field)):
            issues.append(f"{field}: identifier field is missing")
    if not _source_record_id(record, payload):
        issues.append("source_record_id: source record ID is missing")
    for field, field_type in contract.field_types.items():
        raw = payload.get(field)
        if _has_value(raw) and not _matches_type(raw, field_type):
            issues.append(f"{field}: expected {field_type}")
    for field, allowed_values in contract.controlled_vocabularies.items():
        raw = payload.get(field)
        if _has_value(raw) and str(raw) not in set(allowed_values):
            issues.append(f"{field}: value is outside controlled vocabulary")
    for field in _timestamp_fields(payload, contract):
        raw = payload.get(field)
        if _has_value(raw) and not _is_parseable_datetime(raw):
            issues.append(f"{field}: timestamp is not parseable")
    for field in _unit_fields(payload):
        raw = payload.get(field)
        if _has_value(raw) and not _is_parseable_unit(raw):
            issues.append(f"{field}: unit is not parseable")
    issues.extend(_forbidden_field_issues(payload))
    issues.extend(_secret_value_issues(payload))
    return issues


def normalize_record(
    record: dict[str, Any] | ExternalRecordEnvelope,
    contract: DataContract,
) -> dict[str, Any]:
    payload = dict(_payload(record))
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        field_type = contract.field_types.get(key)
        if field_type == "number" and isinstance(value, str):
            normalized[key] = float(value) if "." in value else int(value)
        elif field_type == "boolean" and isinstance(value, str):
            normalized[key] = value.strip().lower() in {"1", "true", "yes", "y"}
        elif field_type == "datetime" and _has_value(value):
            normalized[key] = _normalize_datetime(value)
        elif key in _unit_fields(payload) and isinstance(value, str):
            normalized[key] = _normalize_unit(value)
        else:
            normalized[key] = value
    source_record_id = _source_record_id(record, payload)
    if source_record_id:
        normalized.setdefault("source_record_id", source_record_id)
    return normalized


def infer_contract_from_sample(
    records: Iterable[dict[str, Any] | ExternalRecordEnvelope],
) -> DataContract:
    materialized = list(records)
    payloads = [_payload(record) for record in materialized]
    all_fields = sorted({field for payload in payloads for field in payload})
    required_fields = [
        field for field in all_fields if all(_has_value(payload.get(field)) for payload in payloads)
    ]
    optional_fields = [field for field in all_fields if field not in required_fields]
    object_type = _infer_object_type(materialized)
    field_types = {
        field: _infer_field_type([payload.get(field) for payload in payloads])
        for field in all_fields
    }
    vocabularies = {
        field: sorted(
            {str(payload[field]) for payload in payloads if _has_value(payload.get(field))}
        )
        for field, field_type in field_types.items()
        if field_type == "string"
        and 1
        <= len({str(payload[field]) for payload in payloads if _has_value(payload.get(field))})
        <= 10
    }
    identifier_fields = [
        field
        for field in ["candidate_id", "registry_id", "assay_run_id", "source_record_id"]
        if field in all_fields
    ]
    return DataContract(
        contract_id=f"contract-{uuid.uuid4().hex[:16]}",
        name=f"Inferred {object_type}",
        object_type=object_type,
        version="inferred",
        required_fields=required_fields,
        optional_fields=optional_fields,
        field_types=field_types,
        controlled_vocabularies=vocabularies,
        identifier_fields=identifier_fields,
        validation_rules=[{"rule": "inferred_from_sample", "record_count": len(materialized)}],
        metadata={"inferred_at": datetime.now(UTC).isoformat()},
    )


def export_contract(contract: DataContract, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(contract.model_dump(mode="json"), indent=2, sort_keys=True))


def import_contract(path: str | Path) -> DataContract:
    return DataContract.model_validate_json(Path(path).read_text())


def _payload(record: dict[str, Any] | ExternalRecordEnvelope) -> dict[str, Any]:
    return dict(record.payload) if isinstance(record, ExternalRecordEnvelope) else dict(record)


def _required_fields(contract: DataContract) -> list[str]:
    required = list(contract.required_fields)
    for field in OBJECT_IDENTIFIER_FIELDS.get(contract.object_type, []):
        if field not in required:
            required.append(field)
    return required


def _source_record_id(
    record: dict[str, Any] | ExternalRecordEnvelope,
    payload: dict[str, Any],
) -> str | None:
    if _has_value(payload.get("source_record_id")):
        return str(payload["source_record_id"])
    if _has_value(payload.get("external_record_id")):
        return str(payload["external_record_id"])
    if isinstance(record, ExternalRecordEnvelope) and _has_value(
        record.provenance.source_record_id
    ):
        return record.provenance.source_record_id
    return None


def _timestamp_fields(payload: dict[str, Any], contract: DataContract) -> set[str]:
    fields = {
        field
        for field, field_type in contract.field_types.items()
        if field_type == "datetime" and field in payload
    }
    fields.update(
        field
        for field in payload
        if field.endswith("_at")
        or field.endswith("_time")
        or field in {"timestamp", "created", "updated", "date"}
    )
    return fields


def _unit_fields(payload: dict[str, Any]) -> set[str]:
    return {
        field
        for field in payload
        if field in COMMON_UNIT_FIELDS or field.endswith("_unit") or field.endswith("_units")
    }


def _forbidden_field_issues(payload: dict[str, Any], prefix: str = "") -> list[str]:
    issues: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        normalized = _normalize_key(str(key))
        if any(marker in normalized for marker in FORBIDDEN_FIELD_MARKERS) and _has_value(value):
            issues.append(f"{path}: forbidden protocol/synthesis/dosing field is present")
        if isinstance(value, dict):
            issues.extend(_forbidden_field_issues(value, path))
    return issues


def _secret_value_issues(payload: dict[str, Any], prefix: str = "") -> list[str]:
    issues: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if _secret_key(str(key)) and _has_value(value):
            issues.append(f"{path}: secret-looking field is not allowed")
        if isinstance(value, dict):
            issues.extend(_secret_value_issues(value, path))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    issues.extend(_secret_value_issues(item, f"{path}[{index}]"))
                elif isinstance(item, str) and redact_secret_values(item) != item:
                    issues.append(f"{path}[{index}]: secret-looking value is not allowed")
        elif isinstance(value, str) and redact_secret_values(value) != value:
            issues.append(f"{path}: secret-looking value is not allowed")
    return issues


def _secret_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(
        marker in normalized for marker in ["api_key", "apikey", "password", "secret", "token"]
    )


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _has_value(value: Any) -> bool:
    return value not in (None, "", [])


def _matches_type(value: Any, field_type: str) -> bool:
    if field_type == "any":
        return True
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "datetime":
        return _is_parseable_datetime(value)
    if field_type == "object":
        return isinstance(value, dict)
    if field_type == "array":
        return isinstance(value, list)
    return False


def _is_parseable_datetime(value: Any) -> bool:
    if isinstance(value, datetime):
        return True
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    return False


def _normalize_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _is_parseable_unit(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return _normalize_unit(value) in PARSEABLE_UNITS


def _normalize_unit(value: str) -> str:
    normalized = value.strip().replace("μ", "µ")
    lowered = normalized.lower()
    if lowered == "percent":
        return "%"
    return lowered


def _infer_object_type(records: list[dict[str, Any] | ExternalRecordEnvelope]) -> str:
    if records and isinstance(records[0], ExternalRecordEnvelope):
        record_type = records[0].record_type
        if record_type in SUPPORTED_OBJECT_TYPES:
            return record_type
    first = _payload(records[0]) if records else {}
    raw = str(first.get("object_type") or first.get("record_type") or "external_record")
    return raw if raw in SUPPORTED_OBJECT_TYPES else "external_record"


def _infer_field_type(values: list[Any]) -> str:
    present = [value for value in values if _has_value(value)]
    if not present:
        return "any"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    if all(isinstance(value, int | float) and not isinstance(value, bool) for value in present):
        return "number"
    if all(_is_parseable_datetime(value) for value in present):
        return "datetime"
    if all(isinstance(value, dict) for value in present):
        return "object"
    if all(isinstance(value, list) for value in present):
        return "array"
    return "string"


def _split_issue(message: str) -> tuple[str | None, str]:
    if ": " not in message:
        return None, message
    field, issue = message.split(": ", 1)
    return field, issue
