from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from molecule_ranker.experiments.importers import (
    import_assay_results_csv,
    import_assay_results_json,
    normalize_date,
    parse_measured_value,
    parse_replicate_values,
)


def test_import_valid_csv_preserves_raw_row_and_infers_context(tmp_path):
    csv_path = tmp_path / "assay_results.csv"
    csv_path.write_text(
        "\n".join(
            [
                (
                    "candidate_name,candidate_id,candidate_origin,canonical_smiles,inchi_key,"
                    "disease_name,target_symbol,assay_name,assay_type,endpoint_name,"
                    "endpoint_category,measured_value,unit,relation,outcome_label,"
                    "activity_direction,replicate_count,replicate_values,uncertainty,"
                    "qc_status,result_date,source_record_id,notes"
                ),
                (
                    "Rasagiline,CHEMBL887,existing,C#CCN1CCC2=CC=CC=C21,"
                    "RUYUTDCTDCBNSZ-UHFFFAOYSA-N,Parkinson disease,MAOB,"
                    "Binding screen,biochemical,binding_affinity,potency,12.5,nM,<=,"
                    "positive,active,2,\"11.9;13.1\",0.4,passed,2026-01-02,row-1,"
                    "Imported user result"
                ),
            ]
        )
        + "\n"
    )

    results = import_assay_results_csv(csv_path, imported_by="analyst-1")

    assert len(results) == 1
    result = results[0]
    assert result.candidate_name == "Rasagiline"
    assert result.result_id.startswith("csv-import-")
    assert result.assay_context.endpoint.name == "binding_affinity"
    assert result.assay_context.endpoint.directionality == "lower_is_better"
    assert result.measured_value == 12.5
    assert result.measured_value_numeric == 12.5
    assert result.replicate_values == [11.9, 13.1]
    assert result.result_date == date(2026, 1, 2)
    assert result.imported_by == "analyst-1"
    assert result.metadata["raw_row"]["source_record_id"] == "row-1"


def test_import_valid_json_accepts_schema_payload(tmp_path):
    json_path = tmp_path / "assay_results.json"
    json_path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "result_id": "result-json-1",
                        "candidate_name": "Safinamide",
                        "candidate_origin": "existing",
                        "candidate_id": "CHEMBL2103830",
                        "target_symbol": "MAOB",
                        "assay_context": {
                            "assay_context_id": "context-1",
                            "assay_name": "Cellular screen",
                            "assay_type": "cellular",
                            "target_symbol": "MAOB",
                            "endpoint": {
                                "endpoint_id": "endpoint-cellular-activity",
                                "name": "cellular_activity",
                                "endpoint_category": "phenotypic",
                                "directionality": "higher_is_better",
                            },
                        },
                        "measured_value": 0.72,
                        "measured_value_numeric": 0.72,
                        "unit": "relative_activity",
                        "outcome_label": "positive",
                        "activity_direction": "active",
                        "confidence": 0.8,
                        "qc_status": "passed",
                        "source": "json_import",
                        "imported_at": "2026-01-03T04:05:00+00:00",
                    }
                ]
            }
        )
    )

    results = import_assay_results_json(json_path, imported_by="analyst-2")

    assert len(results) == 1
    assert results[0].candidate_name == "Safinamide"
    assert results[0].imported_by == "analyst-2"
    assert results[0].assay_context.assay_type == "cellular"


def test_parse_replicate_values_accepts_strings_lists_and_empty_values():
    assert parse_replicate_values("1.2; 3.4, 5") == [1.2, 3.4, 5.0]
    assert parse_replicate_values([1, "2.5", ""]) == [1.0, 2.5]
    assert parse_replicate_values("") == []
    with pytest.raises(ValueError, match="replicate value is not numeric"):
        parse_replicate_values("1.2;not-a-number")


def test_missing_candidate_name_raises_without_fabricating_identity(tmp_path):
    csv_path = tmp_path / "missing_candidate.csv"
    csv_path.write_text(
        "candidate_id,assay_name,assay_type,endpoint_name,endpoint_category,outcome_label\n"
        "CHEMBL887,Binding screen,biochemical,binding_affinity,potency,positive\n"
    )

    with pytest.raises(ValueError, match="candidate_name is required"):
        import_assay_results_csv(csv_path)


def test_malformed_numeric_value_is_preserved_with_warning(tmp_path):
    csv_path = tmp_path / "malformed_numeric.csv"
    csv_path.write_text(
        (
            "candidate_name,assay_name,assay_type,endpoint_name,endpoint_category,"
            "measured_value,outcome_label,activity_direction,qc_status\n"
        )
        +
        (
            "Rasagiline,Binding screen,biochemical,binding_affinity,potency,"
            "not-a-number,positive,active,passed\n"
        )
    )

    result = import_assay_results_csv(csv_path)[0]

    assert result.measured_value == "not-a-number"
    assert result.measured_value_numeric is None
    assert "measured_value is not numeric" in result.metadata["warnings"]
    assert parse_measured_value("not-a-number") == ("not-a-number", None)


def test_missing_unit_for_numeric_potency_records_warning(tmp_path):
    csv_path = tmp_path / "missing_unit.csv"
    csv_path.write_text(
        (
            "candidate_name,assay_name,assay_type,endpoint_name,endpoint_category,"
            "measured_value,outcome_label,activity_direction,qc_status\n"
        )
        +
        (
            "Rasagiline,Binding screen,biochemical,binding_affinity,potency,"
            "12.5,positive,active,passed\n"
        )
    )

    result = import_assay_results_csv(csv_path)[0]

    assert "unit is missing for numeric potency result" in result.metadata["warnings"]


def test_invalid_outcome_label_is_rejected(tmp_path):
    csv_path = tmp_path / "invalid_outcome.csv"
    csv_path.write_text(
        "candidate_name,assay_name,assay_type,endpoint_name,endpoint_category,outcome_label,activity_direction,qc_status\n"
        "Rasagiline,Binding screen,biochemical,binding_affinity,potency,cured,active,passed\n"
    )

    with pytest.raises(ValidationError):
        import_assay_results_csv(csv_path)


def test_normalize_date_handles_common_inputs():
    assert normalize_date("2026-01-02") == date(2026, 1, 2)
    assert normalize_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert normalize_date("") is None
    with pytest.raises(ValueError, match="could not parse result_date"):
        normalize_date("January-ish")
