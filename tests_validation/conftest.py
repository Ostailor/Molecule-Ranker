from __future__ import annotations

import csv
import json
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from molecule_ranker.contracts import artifact_contract_for_path, validate_artifact_file
from molecule_ranker.validation import run_golden_workflows
from molecule_ranker.validation.golden_workflows import COMMON_FORBIDDEN_OUTPUTS
from molecule_ranker.validation.runner import check_forbidden_outputs
from molecule_ranker.validation.schemas import GoldenWorkflowResult


def run_validation_workflow(tmp_path: Path, workflow_id: str) -> GoldenWorkflowResult:
    report = run_golden_workflows(
        workflow=workflow_id,
        output_dir=tmp_path / "validation",
        live=False,
    )

    assert report.status == "pass"
    assert report.live_validation is False
    assert report.workflow_count == 1
    result = report.results[0]
    assert result.workflow_id == workflow_id
    return result


def assert_release_basics(result: GoldenWorkflowResult) -> None:
    assert result.status == "pass"
    assert result.mode == "test"
    assert result.missing_artifacts == []
    assert result.metadata["external_services"] == "mocked"
    assert result.metadata["synthetic_data"] is True

    for artifact in result.artifacts:
        assert artifact.exists(), artifact
        assert artifact.is_file(), artifact


def assert_contract_artifacts_valid(result: GoldenWorkflowResult) -> None:
    validated = []
    for artifact in result.artifacts:
        if artifact_contract_for_path(artifact) is None:
            continue
        validation = validate_artifact_file(artifact, migrate=False)
        assert validation.valid, validation.as_dict()
        assert not validation.migrated
        validated.append(validation)

    assert validated, f"{result.workflow_id} did not emit any contracted artifacts"


def assert_mocked_default_mode(result: GoldenWorkflowResult) -> None:
    for payload in iter_json_payloads(result):
        assert payload.get("external_services") == "mocked"
        assert payload.get("live_public_apis") is False
        assert payload.get("credentials_required") is False


def assert_provenance_and_source_ids(result: GoldenWorkflowResult) -> None:
    source_record_ids: list[str] = []
    for payload in iter_json_payloads(result):
        provenance = payload.get("provenance")
        assert isinstance(provenance, dict)
        assert provenance.get("source_system") == "synthetic_validation_fixture"
        assert provenance.get("source_record_id")
        source_record_ids.extend(str(value) for value in find_values(payload, "source_record_id"))
    for csv_path in result.artifact_dir.glob("*.csv"):
        with csv_path.open() as handle:
            rows = list(csv.DictReader(handle))
        source_record_ids.extend(
            str(row["source_record_id"]) for row in rows if row.get("source_record_id")
        )

    assert source_record_ids, f"{result.workflow_id} has no source_record_id values"


def assert_reports_have_limitations_and_no_forbidden_claims(result: GoldenWorkflowResult) -> None:
    findings = check_forbidden_outputs(result.artifacts, COMMON_FORBIDDEN_OUTPUTS)
    assert findings == []

    report_like = [
        artifact
        for artifact in result.artifacts
        if artifact.suffix in {".md", ".html"} or "report" in artifact.name
    ]
    assert report_like
    for artifact in report_like:
        text = artifact.read_text(errors="ignore").lower()
        assert "limitations" in text or "internal research use only" in text


def assert_common_release_invariants(result: GoldenWorkflowResult) -> None:
    assert_release_basics(result)
    assert_contract_artifacts_valid(result)
    assert_mocked_default_mode(result)
    assert_provenance_and_source_ids(result)
    assert_reports_have_limitations_and_no_forbidden_claims(result)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    assert isinstance(payload, dict)
    return payload


def load_project_export(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        payload = json.loads(archive.read("project_export.json"))
    assert isinstance(payload, dict)
    return payload


def iter_json_payloads(result: GoldenWorkflowResult) -> Iterable[dict[str, Any]]:
    for artifact in result.artifacts:
        if artifact.suffix != ".json":
            continue
        yield load_json(artifact)
    zip_path = result.artifact_dir / "project_export.zip"
    if zip_path.exists():
        yield load_project_export(zip_path)


def find_values(payload: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(payload, dict):
        for item_key, item_value in payload.items():
            if item_key == key:
                values.append(item_value)
            values.extend(find_values(item_value, key))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(find_values(item, key))
    return values


def has_forbidden_key(payload: Any, forbidden_key: str) -> bool:
    if isinstance(payload, dict):
        return any(
            key == forbidden_key or has_forbidden_key(value, forbidden_key)
            for key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(has_forbidden_key(item, forbidden_key) for item in payload)
    return False
