from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime

import pytest

from molecule_ranker.experiments.schemas import (
    ActiveLearningBatch,
    ActiveLearningSuggestion,
    AssayContext,
    AssayEndpoint,
    AssayResult,
    ExperimentAuditEvent,
)
from molecule_ranker.experiments.store import ExperimentalResultStore

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _context(endpoint_name: str = "binding_affinity") -> AssayContext:
    return AssayContext(
        assay_context_id=f"context-{endpoint_name}",
        assay_name="Binding screen",
        assay_type="biochemical",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        endpoint=AssayEndpoint(
            endpoint_id=f"endpoint-{endpoint_name}",
            name=endpoint_name,
            endpoint_category="potency",
            unit="nM",
            directionality="lower_is_better",
        ),
    )


def _result(
    result_id: str = "result-1",
    *,
    candidate_name: str = "Rasagiline",
    candidate_id: str | None = "CHEMBL887",
    outcome_label: str = "positive",
    activity_direction: str = "active",
    qc_status: str = "passed",
    source_record_id: str | None = "row-1",
) -> AssayResult:
    return AssayResult(
        result_id=result_id,
        run_id="run-1",
        workspace_id="workspace-1",
        review_item_id="review-item-1",
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_origin="existing",
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        inchi_key="RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        assay_context=_context(),
        measured_value=12.5,
        measured_value_numeric=12.5,
        unit="nM",
        normalized_value=12.5,
        normalized_unit="nM",
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        replicate_count=2,
        replicate_values=[11.9, 13.1],
        confidence=0.82,
        qc_status=qc_status,  # type: ignore[arg-type]
        result_date=date(2026, 1, 2),
        source="csv_import",
        source_record_id=source_record_id,
        imported_at=FIXED_TIME,
        imported_by="analyst-1",
        metadata={"raw_row": {"source_record_id": source_record_id}},
    )


def test_store_creates_schema_and_imports_results_with_audit(tmp_path):
    db_path = tmp_path / "experiments.sqlite"
    store = ExperimentalResultStore(db_path)

    imported = store.import_results([_result()], actor="analyst-1")
    loaded = store.get_result("result-1")
    events = store.list_audit_events()

    assert imported[0].result_id == "result-1"
    assert loaded.candidate_name == "Rasagiline"
    assert loaded.source_record_id == "row-1"
    assert events[-1].event_type == "assay_results_imported"
    assert events[-1].actor == "analyst-1"

    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
    assert {
        "assay_results",
        "assay_contexts",
        "experimental_evidence_summaries",
        "active_learning_batches",
        "active_learning_suggestions",
        "experiment_audit_events",
    } <= table_names


def test_store_persists_model_cards_manifests_metrics_and_predictions(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    model_id = "model-binding-affinity-1"
    model_card = {
        "artifact_kind": "model_card",
        "model_id": model_id,
        "endpoint_name": "binding_affinity",
        "evidence_boundary": "not_experimental_evidence",
    }
    training_manifest = {
        "artifact_kind": "training_manifest",
        "model_id": model_id,
        "labels_excluded_from_manifest": True,
    }
    metrics = {"artifact_kind": "model_metrics", "model_id": model_id, "metrics": {}}
    prediction = {
        "artifact_kind": "prediction_artifact",
        "prediction_id": "prediction-1",
        "model_id": model_id,
        "candidate_id": "candidate-1",
        "endpoint_name": "binding_affinity",
        "evidence_boundary": "not_experimental_evidence",
        "metadata": {"not_assay_result": True, "not_experimental_evidence": True},
    }

    store.save_model_artifact(
        model_id=model_id,
        model_card=model_card,
        training_manifest=training_manifest,
        metrics=metrics,
    )
    store.save_prediction_artifacts(model_id=model_id, predictions=[prediction])

    loaded = store.get_model_artifact(model_id)
    predictions = store.list_prediction_artifacts(model_id=model_id)

    assert loaded["model_card"] == model_card
    assert loaded["training_manifest"]["labels_excluded_from_manifest"] is True
    assert loaded["metrics"] == metrics
    assert predictions == [prediction]
    assert store.list_results() == []
    assert all(event.object_type != "EvidenceItem" for event in store.list_audit_events())


def test_store_rejects_duplicate_result_ids_unless_update_enabled(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results([_result()], actor="analyst-1")

    with pytest.raises(ValueError, match="already exists"):
        store.import_results([_result(outcome_label="negative", activity_direction="inactive")])

    store.import_results(
        [_result(outcome_label="negative", activity_direction="inactive")],
        actor="analyst-2",
        update=True,
    )

    assert store.get_result("result-1").outcome_label == "negative"


def test_store_query_surfaces_filter_by_candidate_target_and_status(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result("result-1"),
            _result(
                "result-2",
                candidate_name="Safinamide",
                candidate_id="CHEMBL2103830",
                outcome_label="negative",
                activity_direction="inactive",
                qc_status="partial",
                source_record_id="row-2",
            ),
        ],
        actor="analyst-1",
    )

    assert [item.result_id for item in store.list_results(outcome_label="negative")] == [
        "result-2"
    ]
    assert store.find_results_for_candidate(candidate_id="CHEMBL887")[0].result_id == "result-1"
    assert len(store.find_results_for_candidate(candidate_name="Safinamide")) == 1
    assert len(store.find_results_for_target("MAOB")) == 2
    assert store.list_results(qc_status="partial")[0].candidate_name == "Safinamide"


def test_store_summarizes_candidate_results_and_persists_summary(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result("result-1"),
            _result(
                "result-2",
                outcome_label="negative",
                activity_direction="inactive",
                source_record_id="row-2",
            ),
            _result(
                "result-3",
                outcome_label="failed_qc",
                activity_direction="ambiguous",
                qc_status="failed",
                source_record_id="row-3",
            ),
        ]
    )

    summary = store.summarize_candidate_results("Rasagiline", candidate_id="CHEMBL887")

    assert summary.result_count == 3
    assert summary.positive_count == 1
    assert summary.negative_count == 1
    assert summary.failed_qc_count == 1
    assert summary.endpoint_summaries["binding_affinity"]["result_count"] == 3
    assert "result-1" in summary.best_supporting_results
    assert "result-2" in summary.key_negative_results


def test_store_exports_and_imports_results_json_with_audit(tmp_path):
    source = ExperimentalResultStore(tmp_path / "source.sqlite")
    source.import_results([_result()], actor="analyst-1")
    output_path = tmp_path / "assay_results.json"

    source.export_results_json(output_path)
    payload = json.loads(output_path.read_text())

    target = ExperimentalResultStore(tmp_path / "target.sqlite")
    imported = target.import_results_json(output_path)

    assert payload["results"][0]["source_record_id"] == "row-1"
    assert imported[0].result_id == "result-1"
    assert target.get_result("result-1").source_record_id == "row-1"
    assert target.list_audit_events()[-1].event_type == "assay_results_json_imported"


def test_store_saves_and_loads_active_learning_batch(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    suggestion = ActiveLearningSuggestion(
        suggestion_id="suggestion-1",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbol="MAOB",
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        acquisition_score=0.7,
        acquisition_strategy="evidence_gap",
        rationale="Needs imported experimental evidence.",
        uncertainty_score=0.5,
        diversity_score=0.2,
        expected_value_score=0.6,
        risk_penalty=0.1,
        constraints_satisfied=True,
        warnings=[],
    )
    batch = ActiveLearningBatch(
        batch_id="batch-1",
        created_at=FIXED_TIME,
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        endpoint_name="binding_affinity",
        strategy="balanced",
        suggestions=[suggestion],
        excluded_candidates=[],
    )

    store.save_active_learning_batch(batch)
    loaded = store.get_active_learning_batch("batch-1")

    assert loaded.suggestions[0].suggestion_id == "suggestion-1"
    assert store.list_audit_events()[-1].event_type == "active_learning_batch_saved"


def test_store_write_audit_event_round_trips(tmp_path):
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    event = ExperimentAuditEvent(
        event_id="audit-1",
        event_type="manual_note",
        actor=None,
        timestamp=FIXED_TIME,
        object_type="AssayResult",
        object_id="result-1",
        summary="Manual audit note.",
        metadata={"reason": "unit test"},
    )

    store.write_audit_event(event)

    loaded = store.list_audit_events()[0]
    assert loaded.event_id == "audit-1"
    assert loaded.metadata["reason"] == "unit test"
