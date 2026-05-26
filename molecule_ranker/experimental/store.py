from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from molecule_ranker.experimental.schemas import AssayResult, ExperimentSummaryReport


class ExperimentalResultStore:
    """SQLite store for imported assay results and experimental audit events."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def import_results(
        self,
        results: list[AssayResult],
        *,
        actor: str = "ExperimentalResultStore",
    ) -> int:
        with self._connect() as connection:
            imported = 0
            for result in results:
                self._upsert_result(connection, result)
                imported += 1
            self._insert_audit_event(
                connection,
                event_type="assay_results_imported",
                actor=actor,
                summary=f"Imported {imported} assay result(s).",
                metadata={"result_ids": [result.result_id for result in results]},
            )
        return imported

    def list_results(
        self,
        *,
        candidate_id: str | None = None,
        molecule_name: str | None = None,
        experiment_id: str | None = None,
        validation_status: str | None = None,
    ) -> list[AssayResult]:
        clauses: list[str] = []
        params: list[str] = []
        if candidate_id:
            clauses.append("(candidate_id = ? or linked_candidate_id = ?)")
            params.extend([candidate_id, candidate_id])
        if molecule_name:
            clauses.append(
                "(lower(molecule_name) = lower(?) or lower(linked_candidate_name) = lower(?))"
            )
            params.extend([molecule_name, molecule_name])
        if experiment_id:
            clauses.append("experiment_id = ?")
            params.append(experiment_id)
        if validation_status:
            clauses.append("validation_status = ?")
            params.append(validation_status)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"select payload_json from assay_results {where} order by imported_at, source_row",
                params,
            ).fetchall()
        return [AssayResult.model_validate_json(row["payload_json"]) for row in rows]

    def summarize(self) -> ExperimentSummaryReport:
        results = self.list_results()
        outcome_counts: dict[str, int] = {}
        experiments: dict[str, dict[str, Any]] = {}
        assay_names = set()
        for result in results:
            if result.outcome is not None:
                outcome_counts[result.outcome] = outcome_counts.get(result.outcome, 0) + 1
            if result.assay_name:
                assay_names.add(result.assay_name)
            experiment_id = result.experiment_id or "unknown"
            experiment = experiments.setdefault(
                experiment_id,
                {
                    "experiment_id": experiment_id,
                    "result_count": 0,
                    "outcome_counts": {},
                    "assay_names": set(),
                },
            )
            experiment["result_count"] += 1
            if result.outcome is not None:
                experiment["outcome_counts"][result.outcome] = (
                    experiment["outcome_counts"].get(result.outcome, 0) + 1
                )
            if result.assay_name:
                experiment["assay_names"].add(result.assay_name)
        experiment_rows = []
        for experiment in experiments.values():
            experiment_rows.append(
                {
                    **experiment,
                    "assay_names": sorted(experiment["assay_names"]),
                    "outcome_counts": dict(sorted(experiment["outcome_counts"].items())),
                }
            )
        return ExperimentSummaryReport(
            result_count=len(results),
            valid_count=sum(1 for result in results if result.validation_status == "valid"),
            incomplete_count=sum(
                1 for result in results if result.validation_status == "incomplete"
            ),
            invalid_count=sum(1 for result in results if result.validation_status == "invalid"),
            experiment_count=len(
                {result.experiment_id for result in results if result.experiment_id}
            ),
            assay_count=len(assay_names),
            linked_candidate_count=len(
                {
                    result.linked_candidate_id or result.candidate_id
                    for result in results
                    if result.linked_candidate_id or result.candidate_id
                }
            ),
            generated_link_count=sum(
                1 for result in results if result.linked_generated_molecule_name
            ),
            review_link_count=sum(1 for result in results if result.linked_review_item_id),
            validation_handoff_link_count=sum(
                1 for result in results if result.linked_validation_handoff_id
            ),
            outcome_counts=dict(sorted(outcome_counts.items())),
            experiments=sorted(experiment_rows, key=lambda item: str(item["experiment_id"])),
        )

    def audit_events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select event_type, actor, summary, metadata_json, created_at
                from experimental_audit_events
                order by created_at
                """
            ).fetchall()
        return [
            {
                "event_type": str(row["event_type"]),
                "actor": str(row["actor"]),
                "summary": str(row["summary"]),
                "metadata": json.loads(str(row["metadata_json"] or "{}")),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists assay_results (
                    result_id text primary key,
                    experiment_id text,
                    assay_name text,
                    molecule_name text,
                    candidate_id text,
                    generated_molecule_name text,
                    target_symbol text,
                    disease_name text,
                    review_item_id text,
                    validation_handoff_id text,
                    linked_candidate_name text,
                    linked_candidate_id text,
                    linked_review_item_id text,
                    linked_validation_handoff_id text,
                    outcome text,
                    validation_status text not null,
                    imported_at text not null,
                    source_path text,
                    source_row integer,
                    payload_json text not null
                );

                create table if not exists experimental_audit_events (
                    event_id integer primary key autoincrement,
                    event_type text not null,
                    actor text not null,
                    summary text not null,
                    metadata_json text not null,
                    created_at text not null default current_timestamp
                );

                create index if not exists idx_assay_results_candidate
                    on assay_results(
                        candidate_id,
                        linked_candidate_id,
                        molecule_name,
                        linked_candidate_name
                    );
                create index if not exists idx_assay_results_experiment
                    on assay_results(experiment_id, assay_name, outcome, validation_status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _upsert_result(self, connection: sqlite3.Connection, result: AssayResult) -> None:
        connection.execute(
            """
            insert or replace into assay_results (
                result_id,
                experiment_id,
                assay_name,
                molecule_name,
                candidate_id,
                generated_molecule_name,
                target_symbol,
                disease_name,
                review_item_id,
                validation_handoff_id,
                linked_candidate_name,
                linked_candidate_id,
                linked_review_item_id,
                linked_validation_handoff_id,
                outcome,
                validation_status,
                imported_at,
                source_path,
                source_row,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.result_id,
                result.experiment_id,
                result.assay_name,
                result.molecule_name,
                result.candidate_id,
                result.generated_molecule_name,
                result.target_symbol,
                result.disease_name,
                result.review_item_id,
                result.validation_handoff_id,
                result.linked_candidate_name,
                result.linked_candidate_id,
                result.linked_review_item_id,
                result.linked_validation_handoff_id,
                result.outcome,
                result.validation_status,
                result.imported_at.isoformat(),
                result.source_path,
                result.source_row,
                result.model_dump_json(),
            ),
        )

    def _insert_audit_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        actor: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            insert into experimental_audit_events (event_type, actor, summary, metadata_json)
            values (?, ?, ?, ?)
            """,
            (event_type, actor, summary, json.dumps(metadata, sort_keys=True)),
        )
