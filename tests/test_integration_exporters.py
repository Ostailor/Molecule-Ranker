from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path

import pytest

from molecule_ranker.integrations.exporters import (
    GENERATED_MOLECULE_WARNING,
    ExportPermissionError,
    create_export_package,
)

FORBIDDEN_PATTERNS = [
    r"\bsynthesis\b",
    r"\bprotocol\b",
    r"\bdosing\b",
    r"\bdose\b",
    r"\bmg/kg\b",
    r"\breagent\b",
    r"\breaction condition\b",
    r"\bpatient treatment\b",
]


def test_export_package_generated(tmp_path: Path) -> None:
    result = create_export_package(
        "candidate_summary_package",
        {
            "org_id": "org-1",
            "project_id": "project-1",
            "candidate_id": "cand-1",
            "candidate_name": "Candidate A",
            "summary": "Research handoff summary.",
        },
        tmp_path / "candidate-package",
        data_contract_version="contract-v1",
        external_system_target={
            "external_system_id": "benchling-1",
            "name": "Benchling sandbox",
        },
    )

    output_dir = Path(result.output_dir)
    assert result.package_type == "candidate_summary_package"
    assert result.data_contract_version == "contract-v1"
    assert result.external_write_ready is False
    assert result.target_metadata["external_system_id"] == "benchling-1"
    assert (output_dir / "package.json").exists()
    assert (output_dir / "package.md").exists()
    assert (output_dir / "manifest.csv").exists()
    assert result.zip_path is not None
    with zipfile.ZipFile(result.zip_path) as archive:
        assert {"package.json", "package.md", "manifest.csv", "manifest.json"} <= set(
            archive.namelist()
        )


def test_generated_molecules_warning_present(tmp_path: Path) -> None:
    result = create_export_package(
        "generated_molecule_package",
        {
            "generated_molecules": [
                {
                    "generated_molecule_id": "gen-1",
                    "name": "GeneratedExample",
                    "canonical_smiles": "CCO",
                }
            ]
        },
        tmp_path / "generated-package",
        formats=["json", "markdown", "csv_manifest"],
    )

    payload = json.loads((Path(result.output_dir) / "package.json").read_text())
    assert GENERATED_MOLECULE_WARNING in payload["warnings"]
    assert (
        payload["content"]["generated_molecules"][0]["hypothesis_label"]
        == "computational_hypothesis"
    )
    assert GENERATED_MOLECULE_WARNING in (Path(result.output_dir) / "package.md").read_text()


def test_no_forbidden_protocol_synthesis_or_dosing_text(tmp_path: Path) -> None:
    result = create_export_package(
        "validation_handoff_package",
        {
            "handoff_id": "handoff-1",
            "summary": (
                "Contains a lab protocol, synthesis route, dosing note, reagents, "
                "reaction conditions, and patient treatment text."
            ),
            "protocol_steps": ["mix sample"],
            "synthesis_instructions": "make the molecule",
            "dose": "10 mg/kg",
        },
        tmp_path / "handoff-package",
    )

    serialized = "\n".join(
        path.read_text(errors="replace")
        for path in Path(result.output_dir).glob("*")
        if path.is_file()
    ).lower()
    offenders = [pattern for pattern in FORBIDDEN_PATTERNS if re.search(pattern, serialized)]
    assert offenders == []


def test_manifest_includes_hashes(tmp_path: Path) -> None:
    result = create_export_package(
        "assay_result_summary_package",
        {
            "assay_results": [
                {
                    "result_id": "result-1",
                    "candidate_id": "cand-1",
                    "assay_context": {
                        "assay_name": "Binding summary",
                        "endpoint": {"name": "Example endpoint"},
                    },
                    "outcome_label": "positive",
                    "measured_value": 12.3,
                    "unit": "nM",
                    "qc_status": "passed",
                    "source_record_id": "row-1",
                }
            ]
        },
        tmp_path / "assay-package",
        formats=["json", "markdown", "csv_manifest"],
    )

    package_payload = json.loads((Path(result.output_dir) / "package.json").read_text())
    assay_result = package_payload["content"]["assay_results"][0]
    assert assay_result["assay_name"] == "Binding summary"
    assert assay_result["endpoint_name"] == "Example endpoint"
    assert assay_result["qc_status"] == "passed"

    with Path(result.manifest_path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["path"] for row in rows} >= {"package.json", "package.md"}
    assert all(len(row["sha256"]) == 64 for row in rows)
    assert result.sha256["package.json"]


def test_external_write_requires_explicit_permission(tmp_path: Path) -> None:
    with pytest.raises(ExportPermissionError):
        create_export_package(
            "review_dossier_package",
            {"review_item_id": "review-1"},
            tmp_path / "review-package",
            external_write=True,
            explicit_permission=False,
        )
