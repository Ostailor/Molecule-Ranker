from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.schemas import (
    ActiveLearningBatch,
    ActiveLearningSuggestion,
    AssayContext,
    AssayResult,
    ExperimentalEvidenceSummary,
    ExperimentAuditEvent,
)


class ExperimentalResultStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.create_schema()

    def create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists assay_contexts (
                    assay_context_id text primary key,
                    assay_name text not null,
                    assay_type text not null,
                    target_symbol text,
                    disease_name text,
                    endpoint_name text not null,
                    endpoint_category text not null,
                    payload_json text not null
                );

                create table if not exists assay_results (
                    result_id text primary key,
                    candidate_name text not null,
                    candidate_id text,
                    candidate_origin text not null,
                    inchi_key text,
                    disease_name text,
                    target_symbol text,
                    assay_name text not null,
                    endpoint_name text not null,
                    outcome_label text not null,
                    activity_direction text not null,
                    qc_status text not null,
                    result_date text,
                    imported_at text not null,
                    assay_context_id text not null,
                    source_record_id text,
                    payload_json text not null,
                    foreign key (assay_context_id) references assay_contexts(assay_context_id)
                );

                create table if not exists experimental_evidence_summaries (
                    summary_id text primary key,
                    candidate_name text not null,
                    candidate_id text,
                    inchi_key text,
                    generated_at text not null,
                    payload_json text not null
                );

                create table if not exists active_learning_batches (
                    batch_id text primary key,
                    created_at text not null,
                    disease_name text,
                    target_symbol text,
                    endpoint_name text not null,
                    strategy text not null,
                    payload_json text not null
                );

                create table if not exists active_learning_suggestions (
                    suggestion_id text primary key,
                    batch_id text not null,
                    candidate_name text not null,
                    candidate_id text,
                    candidate_origin text not null,
                    target_symbol text,
                    acquisition_score real not null,
                    acquisition_strategy text not null,
                    payload_json text not null,
                    foreign key (batch_id) references active_learning_batches(batch_id)
                );

                create table if not exists experiment_audit_events (
                    event_id text primary key,
                    event_type text not null,
                    actor text,
                    timestamp text not null,
                    object_type text not null,
                    object_id text not null,
                    summary text not null,
                    payload_json text not null
                );

                create table if not exists model_artifacts (
                    model_id text primary key,
                    endpoint_name text,
                    target_symbol text,
                    disease_name text,
                    created_at text not null,
                    model_card_json text not null,
                    training_manifest_json text not null,
                    metrics_json text not null
                );

                create table if not exists model_prediction_artifacts (
                    prediction_id text primary key,
                    model_id text not null,
                    candidate_id text,
                    endpoint_name text,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (model_id) references model_artifacts(model_id)
                );

                create index if not exists idx_assay_results_candidate
                    on assay_results(candidate_name, candidate_id, candidate_origin, inchi_key);
                create index if not exists idx_assay_results_target
                    on assay_results(target_symbol, disease_name, assay_name, endpoint_name);
                create index if not exists idx_assay_results_outcome
                    on assay_results(outcome_label, activity_direction, qc_status);
                create index if not exists idx_assay_results_dates
                    on assay_results(result_date, imported_at);
                create index if not exists idx_model_predictions_model
                    on model_prediction_artifacts(model_id, endpoint_name, candidate_id);
                """
            )

    def import_results(
        self,
        results: list[AssayResult],
        actor: str | None = None,
        *,
        update: bool = False,
    ) -> list[AssayResult]:
        with self._connect() as connection:
            for result in results:
                if not update and self._result_exists(connection, result.result_id):
                    raise ValueError(f"Assay result already exists: {result.result_id}")
            for result in results:
                self._upsert_assay_context(connection, result.assay_context)
                self._upsert_result(connection, result)
            self._insert_audit_event(
                connection,
                _audit_event(
                    event_type="assay_results_imported",
                    actor=actor,
                    object_type="AssayResult",
                    object_id=",".join(result.result_id for result in results) or "none",
                    summary=f"Imported {len(results)} assay result(s).",
                    metadata={
                        "result_ids": [result.result_id for result in results],
                        "update": update,
                    },
                ),
            )
        return results

    def get_result(self, result_id: str) -> AssayResult:
        with self._connect() as connection:
            row = connection.execute(
                "select payload_json from assay_results where result_id = ?",
                (result_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown assay result: {result_id}")
        return AssayResult.model_validate_json(row["payload_json"])

    def list_results(
        self,
        *,
        candidate_name: str | None = None,
        candidate_id: str | None = None,
        candidate_origin: str | None = None,
        inchi_key: str | None = None,
        disease_name: str | None = None,
        target_symbol: str | None = None,
        assay_name: str | None = None,
        endpoint_name: str | None = None,
        outcome_label: str | None = None,
        activity_direction: str | None = None,
        qc_status: str | None = None,
        result_date: str | None = None,
    ) -> list[AssayResult]:
        filters = {
            "candidate_name": candidate_name,
            "candidate_id": candidate_id,
            "candidate_origin": candidate_origin,
            "inchi_key": inchi_key,
            "disease_name": disease_name,
            "target_symbol": target_symbol,
            "assay_name": assay_name,
            "endpoint_name": endpoint_name,
            "outcome_label": outcome_label,
            "activity_direction": activity_direction,
            "qc_status": qc_status,
            "result_date": result_date,
        }
        where, params = _where_equals(filters)
        with self._connect() as connection:
            rows = connection.execute(
                f"select payload_json from assay_results {where} order by imported_at, result_id",
                params,
            ).fetchall()
        return [AssayResult.model_validate_json(row["payload_json"]) for row in rows]

    def find_results_for_candidate(
        self,
        candidate_name: str | None = None,
        candidate_id: str | None = None,
        inchi_key: str | None = None,
    ) -> list[AssayResult]:
        clauses: list[str] = []
        params: list[str] = []
        if candidate_name:
            clauses.append("candidate_name = ?")
            params.append(candidate_name)
        if candidate_id:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        if inchi_key:
            clauses.append("inchi_key = ?")
            params.append(inchi_key)
        if not clauses:
            raise ValueError("candidate_name, candidate_id, or inchi_key is required")
        with self._connect() as connection:
            rows = connection.execute(
                "select payload_json from assay_results where "
                + " or ".join(clauses)
                + " order by imported_at, result_id",
                params,
            ).fetchall()
        return [AssayResult.model_validate_json(row["payload_json"]) for row in rows]

    def find_results_for_target(self, target_symbol: str) -> list[AssayResult]:
        return self.list_results(target_symbol=target_symbol)

    def summarize_candidate_results(
        self,
        candidate_name: str,
        candidate_id: str | None = None,
        inchi_key: str | None = None,
    ) -> ExperimentalEvidenceSummary:
        results = self.find_results_for_candidate(
            candidate_name=candidate_name,
            candidate_id=candidate_id,
            inchi_key=inchi_key,
        )
        counts = Counter(result.outcome_label for result in results)
        endpoint_summaries: dict[str, dict[str, Any]] = {}
        for result in results:
            endpoint_name = result.assay_context.endpoint.name
            endpoint_summary = endpoint_summaries.setdefault(
                endpoint_name,
                {"result_count": 0, "outcome_counts": {}},
            )
            endpoint_summary["result_count"] += 1
            outcome_counts = endpoint_summary["outcome_counts"]
            outcome_counts[result.outcome_label] = outcome_counts.get(result.outcome_label, 0) + 1
        confidence = (
            round(sum(result.confidence for result in results) / len(results), 3)
            if results
            else 0.0
        )
        summary = ExperimentalEvidenceSummary(
            candidate_id=(
                candidate_id or _first_non_empty(result.candidate_id for result in results)
            ),
            candidate_name=candidate_name,
            candidate_origin=results[0].candidate_origin if results else "unknown",
            result_count=len(results),
            positive_count=counts.get("positive", 0),
            negative_count=counts.get("negative", 0),
            inconclusive_count=counts.get("inconclusive", 0),
            failed_qc_count=counts.get("failed_qc", 0),
            endpoint_summaries=endpoint_summaries,
            best_supporting_results=[
                result.result_id for result in results if result.outcome_label == "positive"
            ],
            key_negative_results=[
                result.result_id for result in results if result.outcome_label == "negative"
            ],
            safety_concerns=[
                result.result_id for result in results if result.activity_direction == "toxic"
            ],
            confidence=confidence,
            interpretation=_interpret_summary(counts),
            warnings=[
                "failed_qc results are retained for audit and quality tracking"
            ]
            if counts.get("failed_qc", 0)
            else [],
            metadata={"result_ids": [result.result_id for result in results]},
        )
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into experimental_evidence_summaries (
                    summary_id, candidate_name, candidate_id, inchi_key, generated_at, payload_json
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    _summary_id(candidate_name, candidate_id, inchi_key),
                    summary.candidate_name,
                    summary.candidate_id,
                    inchi_key,
                    datetime.now(UTC).isoformat(),
                    summary.model_dump_json(),
                ),
            )
        return summary

    def save_active_learning_batch(self, batch: ActiveLearningBatch) -> ActiveLearningBatch:
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into active_learning_batches (
                    batch_id, created_at, disease_name, target_symbol, endpoint_name,
                    strategy, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.created_at.isoformat(),
                    batch.disease_name,
                    batch.target_symbol,
                    batch.endpoint_name,
                    batch.strategy,
                    batch.model_dump_json(),
                ),
            )
            connection.execute(
                "delete from active_learning_suggestions where batch_id = ?",
                (batch.batch_id,),
            )
            for suggestion in batch.suggestions:
                self._insert_suggestion(connection, batch.batch_id, suggestion)
            self._insert_audit_event(
                connection,
                _audit_event(
                    event_type="active_learning_batch_saved",
                    actor=None,
                    object_type="ActiveLearningBatch",
                    object_id=batch.batch_id,
                    summary=f"Saved active-learning batch {batch.batch_id}.",
                    metadata={"suggestion_count": len(batch.suggestions)},
                ),
            )
        return batch

    def get_active_learning_batch(self, batch_id: str) -> ActiveLearningBatch:
        with self._connect() as connection:
            row = connection.execute(
                "select payload_json from active_learning_batches where batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown active-learning batch: {batch_id}")
        return ActiveLearningBatch.model_validate_json(row["payload_json"])

    def save_model_artifact(
        self,
        *,
        model_id: str,
        model_card: dict[str, Any],
        training_manifest: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        _assert_model_artifact_boundary(model_card)
        _assert_model_artifact_boundary(training_manifest)
        _assert_model_artifact_boundary(metrics)
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into model_artifacts (
                    model_id, endpoint_name, target_symbol, disease_name, created_at,
                    model_card_json, training_manifest_json, metrics_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    model_card.get("endpoint_name")
                    or training_manifest.get("assay_scope", {}).get("endpoint_name"),
                    model_card.get("target_symbol")
                    or training_manifest.get("assay_scope", {}).get("target_symbol"),
                    model_card.get("disease_name")
                    or training_manifest.get("assay_scope", {}).get("disease_name"),
                    now.isoformat(),
                    json.dumps(model_card, sort_keys=True),
                    json.dumps(training_manifest, sort_keys=True),
                    json.dumps(metrics, sort_keys=True),
                ),
            )
            self._insert_audit_event(
                connection,
                _audit_event(
                    event_type="model_artifact_saved",
                    actor=None,
                    object_type="ModelArtifact",
                    object_id=model_id,
                    summary=f"Saved model artifact {model_id}.",
                    metadata={"artifact_kinds": ["model_card", "training_manifest", "metrics"]},
                ),
            )
        return self.get_model_artifact(model_id)

    def get_model_artifact(self, model_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "select * from model_artifacts where model_id = ?",
                (model_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown model artifact: {model_id}")
        return {
            "model_id": row["model_id"],
            "model_card": json.loads(row["model_card_json"]),
            "training_manifest": json.loads(row["training_manifest_json"]),
            "metrics": json.loads(row["metrics_json"]),
        }

    def list_model_artifacts(
        self,
        *,
        endpoint_name: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _where_equals({"endpoint_name": endpoint_name})
        with self._connect() as connection:
            rows = connection.execute(
                f"select model_id from model_artifacts {where} order by created_at, model_id",
                params,
            ).fetchall()
        return [self.get_model_artifact(row["model_id"]) for row in rows]

    def save_prediction_artifacts(
        self,
        *,
        model_id: str,
        predictions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            existing = connection.execute(
                "select 1 from model_artifacts where model_id = ?",
                (model_id,),
            ).fetchone()
            if existing is None:
                raise ValueError(f"Unknown model artifact: {model_id}")
            for prediction in predictions:
                _assert_prediction_boundary(prediction)
                prediction_id = str(
                    prediction.get("prediction_id")
                    or _hashed_id(
                        "prediction",
                        model_id,
                        prediction.get("candidate_id"),
                        prediction.get("candidate_name"),
                        prediction.get("endpoint_name"),
                    )
                )
                payload = {**prediction, "prediction_id": prediction_id, "model_id": model_id}
                connection.execute(
                    """
                    insert or replace into model_prediction_artifacts (
                        prediction_id, model_id, candidate_id, endpoint_name, created_at,
                        payload_json
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prediction_id,
                        model_id,
                        payload.get("candidate_id"),
                        payload.get("endpoint_name"),
                        datetime.now(UTC).isoformat(),
                        json.dumps(payload, sort_keys=True),
                    ),
                )
            self._insert_audit_event(
                connection,
                _audit_event(
                    event_type="model_prediction_artifacts_saved",
                    actor=None,
                    object_type="ModelPredictionArtifact",
                    object_id=model_id,
                    summary=f"Saved {len(predictions)} model prediction artifact(s).",
                    metadata={"model_id": model_id, "prediction_count": len(predictions)},
                ),
            )
        return self.list_prediction_artifacts(model_id=model_id)

    def list_prediction_artifacts(
        self,
        *,
        model_id: str | None = None,
        endpoint_name: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _where_equals({"model_id": model_id, "endpoint_name": endpoint_name})
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select payload_json from model_prediction_artifacts
                {where}
                order by created_at, prediction_id
                """,
                params,
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def export_results_json(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        results = self.list_results()
        payload = {"results": [result.model_dump(mode="json") for result in results]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def import_results_json(self, input_path: str | Path) -> list[AssayResult]:
        path = Path(input_path)
        payload = json.loads(path.read_text())
        raw_results = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(raw_results, list):
            raise ValueError("Experiment result JSON must contain a results list.")
        results = [AssayResult.model_validate(raw) for raw in raw_results]
        imported = self.import_results(results, actor="ExperimentalResultStore")
        self.write_audit_event(
            _audit_event(
                event_type="assay_results_json_imported",
                actor="ExperimentalResultStore",
                object_type="AssayResult",
                object_id=",".join(result.result_id for result in results) or "none",
                summary=f"Imported {len(results)} assay result(s) from JSON.",
                metadata={"input_path": str(path)},
            )
        )
        return imported

    def write_audit_event(self, event: ExperimentAuditEvent) -> ExperimentAuditEvent:
        with self._connect() as connection:
            self._insert_audit_event(connection, event)
        return event

    def list_audit_events(self) -> list[ExperimentAuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select payload_json from experiment_audit_events
                order by timestamp, event_id
                """
            ).fetchall()
        return [ExperimentAuditEvent.model_validate_json(row["payload_json"]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return connection

    def _result_exists(self, connection: sqlite3.Connection, result_id: str) -> bool:
        row = connection.execute(
            "select 1 from assay_results where result_id = ?",
            (result_id,),
        ).fetchone()
        return row is not None

    def _upsert_assay_context(
        self,
        connection: sqlite3.Connection,
        context: AssayContext,
    ) -> None:
        connection.execute(
            """
            insert or replace into assay_contexts (
                assay_context_id, assay_name, assay_type, target_symbol, disease_name,
                endpoint_name, endpoint_category, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.assay_context_id,
                context.assay_name,
                context.assay_type,
                context.target_symbol,
                context.disease_name,
                context.endpoint.name,
                context.endpoint.endpoint_category,
                context.model_dump_json(),
            ),
        )

    def _upsert_result(self, connection: sqlite3.Connection, result: AssayResult) -> None:
        connection.execute(
            """
            insert or replace into assay_results (
                result_id,
                candidate_name,
                candidate_id,
                candidate_origin,
                inchi_key,
                disease_name,
                target_symbol,
                assay_name,
                endpoint_name,
                outcome_label,
                activity_direction,
                qc_status,
                result_date,
                imported_at,
                assay_context_id,
                source_record_id,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.result_id,
                result.candidate_name,
                result.candidate_id,
                result.candidate_origin,
                result.inchi_key,
                result.disease_name,
                result.target_symbol,
                result.assay_context.assay_name,
                result.assay_context.endpoint.name,
                result.outcome_label,
                result.activity_direction,
                result.qc_status,
                result.result_date.isoformat() if result.result_date else None,
                result.imported_at.isoformat(),
                result.assay_context.assay_context_id,
                result.source_record_id,
                result.model_dump_json(),
            ),
        )

    def _insert_suggestion(
        self,
        connection: sqlite3.Connection,
        batch_id: str,
        suggestion: ActiveLearningSuggestion,
    ) -> None:
        connection.execute(
            """
            insert or replace into active_learning_suggestions (
                suggestion_id, batch_id, candidate_name, candidate_id, candidate_origin,
                target_symbol, acquisition_score, acquisition_strategy, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                suggestion.suggestion_id,
                batch_id,
                suggestion.candidate_name,
                suggestion.candidate_id,
                suggestion.candidate_origin,
                suggestion.target_symbol,
                suggestion.acquisition_score,
                suggestion.acquisition_strategy,
                suggestion.model_dump_json(),
            ),
        )

    def _insert_audit_event(
        self,
        connection: sqlite3.Connection,
        event: ExperimentAuditEvent,
    ) -> None:
        connection.execute(
            """
            insert or replace into experiment_audit_events (
                event_id, event_type, actor, timestamp, object_type, object_id,
                summary, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.actor,
                event.timestamp.isoformat(),
                event.object_type,
                event.object_id,
                event.summary,
                event.model_dump_json(),
            ),
        )


def _where_equals(filters: dict[str, str | None]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for key, value in filters.items():
        if value is None:
            continue
        clauses.append(f"{key} = ?")
        params.append(value)
    return (f"where {' and '.join(clauses)}" if clauses else "", params)


def _audit_event(
    *,
    event_type: str,
    actor: str | None,
    object_type: str,
    object_id: str,
    summary: str,
    metadata: dict[str, Any],
) -> ExperimentAuditEvent:
    timestamp = datetime.now(UTC)
    return ExperimentAuditEvent(
        event_id=_hashed_id(
            "audit",
            event_type,
            actor,
            object_type,
            object_id,
            timestamp.isoformat(),
        ),
        event_type=event_type,
        actor=actor,
        timestamp=timestamp,
        object_type=object_type,
        object_id=object_id,
        summary=summary,
        metadata=metadata,
    )


def _summary_id(
    candidate_name: str,
    candidate_id: str | None,
    inchi_key: str | None,
) -> str:
    return _hashed_id("summary", candidate_name, candidate_id, inchi_key)


def _hashed_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{uuid5(NAMESPACE_URL, raw).hex[:16]}"


def _first_non_empty(values: Any) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


def _interpret_summary(counts: Counter[str]) -> str:
    if counts.get("positive", 0) and counts.get("negative", 0):
        return "Mixed imported experimental outcomes; no clinical efficacy claim is made."
    if counts.get("positive", 0):
        return "Imported experimental outcomes include positive assay results."
    if counts.get("negative", 0):
        return "Imported experimental outcomes include negative assay results."
    if counts.get("failed_qc", 0):
        return "Only failed-QC imported outcomes are available for this candidate."
    return "No decisive imported experimental outcomes are available."


def _assert_model_artifact_boundary(payload: dict[str, Any]) -> None:
    if payload.get("artifact_kind") not in {
        "model_card",
        "training_manifest",
        "model_metrics",
    }:
        raise ValueError("Model artifacts must be cards, manifests, or metrics.")
    if payload.get("evidence_boundary") not in {None, "not_experimental_evidence"}:
        raise ValueError("Model artifacts must not be experimental evidence.")


def _assert_prediction_boundary(payload: dict[str, Any]) -> None:
    if payload.get("artifact_kind") != "prediction_artifact":
        raise ValueError("Model predictions must be prediction artifacts.")
    if payload.get("evidence_boundary") != "not_experimental_evidence":
        raise ValueError("Model predictions must not be experimental evidence.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("not_assay_result"):
        raise ValueError("Model predictions must be labeled as not assay results.")
