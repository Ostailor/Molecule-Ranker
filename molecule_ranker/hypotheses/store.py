from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    HypothesisGenerationRun,
    HypothesisLifecycleEvent,
    HypothesisReviewDecision,
    ResearchHypothesis,
    TestableResearchQuestion,
)

TABLES = (
    "research_hypotheses",
    "testable_research_questions",
    "falsification_criteria",
    "evidence_gaps",
    "hypothesis_review_decisions",
    "hypothesis_lifecycle_events",
    "hypothesis_generation_runs",
)


class HypothesisStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.create_schema()

    def create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists research_hypotheses (
                    hypothesis_id text primary key,
                    hypothesis_type text not null,
                    status text not null,
                    project_id text,
                    program_id text,
                    created_at text not null,
                    updated_at text not null,
                    payload_json text not null
                );

                create table if not exists testable_research_questions (
                    question_id text primary key,
                    hypothesis_id text not null,
                    question_type text not null,
                    payload_json text not null,
                    foreign key (hypothesis_id) references research_hypotheses(hypothesis_id)
                );

                create table if not exists falsification_criteria (
                    criterion_id text primary key,
                    hypothesis_id text not null,
                    evidence_type_needed text not null,
                    payload_json text not null,
                    foreign key (hypothesis_id) references research_hypotheses(hypothesis_id)
                );

                create table if not exists evidence_gaps (
                    gap_id text primary key,
                    hypothesis_id text not null,
                    gap_type text not null,
                    severity text not null,
                    payload_json text not null,
                    foreign key (hypothesis_id) references research_hypotheses(hypothesis_id)
                );

                create table if not exists hypothesis_review_decisions (
                    decision_id text primary key,
                    hypothesis_id text not null,
                    reviewer_id text not null,
                    decision text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (hypothesis_id) references research_hypotheses(hypothesis_id)
                );

                create table if not exists hypothesis_lifecycle_events (
                    event_id text primary key,
                    hypothesis_id text not null,
                    event_type text not null,
                    actor text,
                    timestamp text not null,
                    payload_json text not null,
                    foreign key (hypothesis_id) references research_hypotheses(hypothesis_id)
                );

                create table if not exists hypothesis_generation_runs (
                    generation_run_id text primary key,
                    project_id text,
                    program_id text,
                    graph_build_id text,
                    started_at text not null,
                    completed_at text,
                    payload_json text not null
                );

                create index if not exists idx_hypotheses_project
                    on research_hypotheses(project_id);
                create index if not exists idx_hypotheses_program
                    on research_hypotheses(program_id);
                create index if not exists idx_hypotheses_status_type
                    on research_hypotheses(status, hypothesis_type);
                create index if not exists idx_questions_hypothesis
                    on testable_research_questions(hypothesis_id);
                create index if not exists idx_criteria_hypothesis
                    on falsification_criteria(hypothesis_id);
                create index if not exists idx_gaps_hypothesis
                    on evidence_gaps(hypothesis_id);
                create index if not exists idx_decisions_hypothesis
                    on hypothesis_review_decisions(hypothesis_id);
                create index if not exists idx_events_hypothesis
                    on hypothesis_lifecycle_events(hypothesis_id, timestamp);
                """
            )

    def create_hypothesis(self, hypothesis: ResearchHypothesis) -> ResearchHypothesis:
        self.create_schema()
        with self._connect() as connection:
            if self._hypothesis_exists(connection, hypothesis.hypothesis_id):
                raise ValueError(f"Hypothesis already exists: {hypothesis.hypothesis_id}")
            self._upsert_hypothesis(connection, hypothesis)
            self._insert_lifecycle_event(
                connection,
                HypothesisLifecycleEvent(
                    hypothesis_id=hypothesis.hypothesis_id,
                    event_type="created",
                    actor="HypothesisStore",
                    summary=f"Created hypothesis {hypothesis.hypothesis_id}.",
                    after=hypothesis.model_dump(mode="json"),
                ),
            )
        return hypothesis

    def update_hypothesis(
        self,
        hypothesis_id: str,
        patch: dict[str, Any],
        actor: str,
    ) -> ResearchHypothesis:
        current = self.get_hypothesis(hypothesis_id)
        before = current.model_dump(mode="json")
        update_patch = {**patch, "updated_at": datetime.now(UTC)}
        updated = current.model_copy(update=update_patch)
        updated = ResearchHypothesis.model_validate(updated.model_dump())
        with self._connect() as connection:
            self._upsert_hypothesis(connection, updated)
            self._insert_lifecycle_event(
                connection,
                HypothesisLifecycleEvent(
                    hypothesis_id=hypothesis_id,
                    event_type="updated",
                    actor=actor,
                    summary=f"Updated hypothesis {hypothesis_id}.",
                    before=before,
                    after=updated.model_dump(mode="json"),
                ),
            )
        return updated

    def get_hypothesis(self, hypothesis_id: str) -> ResearchHypothesis:
        with self._connect() as connection:
            row = connection.execute(
                "select payload_json from research_hypotheses where hypothesis_id = ?",
                (hypothesis_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown hypothesis: {hypothesis_id}")
        return ResearchHypothesis.model_validate_json(row["payload_json"])

    def list_hypotheses(
        self,
        project_id: str | None = None,
        program_id: str | None = None,
        status: str | None = None,
        type: str | None = None,
    ) -> list[ResearchHypothesis]:
        filters = {
            "project_id": project_id,
            "program_id": program_id,
            "status": status,
            "hypothesis_type": type,
        }
        where, params = _where_equals(filters)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select payload_json from research_hypotheses
                {where}
                order by updated_at desc, hypothesis_id
                """,
                params,
            ).fetchall()
        return [ResearchHypothesis.model_validate_json(row["payload_json"]) for row in rows]

    def add_research_question(
        self,
        question: TestableResearchQuestion,
    ) -> TestableResearchQuestion:
        self._ensure_hypothesis(question.hypothesis_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into testable_research_questions
                (question_id, hypothesis_id, question_type, payload_json)
                values (?, ?, ?, ?)
                """,
                (
                    question.question_id,
                    question.hypothesis_id,
                    question.question_type,
                    question.model_dump_json(),
                ),
            )
        return question

    def add_falsification_criterion(
        self,
        criterion: FalsificationCriterion,
    ) -> FalsificationCriterion:
        self._ensure_hypothesis(criterion.hypothesis_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into falsification_criteria
                (criterion_id, hypothesis_id, evidence_type_needed, payload_json)
                values (?, ?, ?, ?)
                """,
                (
                    criterion.criterion_id,
                    criterion.hypothesis_id,
                    criterion.evidence_type_needed,
                    criterion.model_dump_json(),
                ),
            )
        return criterion

    def add_evidence_gap(self, gap: EvidenceGap) -> EvidenceGap:
        self._ensure_hypothesis(gap.hypothesis_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into evidence_gaps
                (gap_id, hypothesis_id, gap_type, severity, payload_json)
                values (?, ?, ?, ?, ?)
                """,
                (
                    gap.gap_id,
                    gap.hypothesis_id,
                    gap.gap_type,
                    gap.severity,
                    gap.model_dump_json(),
                ),
            )
        return gap

    def add_review_decision(
        self,
        decision: HypothesisReviewDecision,
    ) -> HypothesisReviewDecision:
        self._ensure_hypothesis(decision.hypothesis_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into hypothesis_review_decisions
                (decision_id, hypothesis_id, reviewer_id, decision, created_at, payload_json)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.hypothesis_id,
                    decision.reviewer_id,
                    decision.decision,
                    _dt(decision.created_at),
                    decision.model_dump_json(),
                ),
            )
            self._insert_lifecycle_event(
                connection,
                HypothesisLifecycleEvent(
                    hypothesis_id=decision.hypothesis_id,
                    event_type="reviewed",
                    actor=decision.reviewer_id,
                    summary=decision.rationale,
                    metadata={
                        "decision_id": decision.decision_id,
                        "decision": decision.decision,
                        "review_decision_is_not_evidence": True,
                    },
                ),
            )
        return decision

    def add_lifecycle_event(
        self,
        event: HypothesisLifecycleEvent,
    ) -> HypothesisLifecycleEvent:
        self._ensure_hypothesis(event.hypothesis_id)
        with self._connect() as connection:
            self._insert_lifecycle_event(connection, event)
        return event

    def add_generation_run(self, run: HypothesisGenerationRun) -> HypothesisGenerationRun:
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into hypothesis_generation_runs
                (
                    generation_run_id, project_id, program_id, graph_build_id,
                    started_at, completed_at, payload_json
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.generation_run_id,
                    run.project_id,
                    run.program_id,
                    run.graph_build_id,
                    _dt(run.started_at),
                    _dt(run.completed_at),
                    run.model_dump_json(),
                ),
            )
        return run

    def list_research_questions(
        self,
        hypothesis_id: str | None = None,
    ) -> list[TestableResearchQuestion]:
        return self._list_payloads(
            "testable_research_questions",
            TestableResearchQuestion,
            hypothesis_id=hypothesis_id,
            order_by="question_id",
        )

    def list_falsification_criteria(
        self,
        hypothesis_id: str | None = None,
    ) -> list[FalsificationCriterion]:
        return self._list_payloads(
            "falsification_criteria",
            FalsificationCriterion,
            hypothesis_id=hypothesis_id,
            order_by="criterion_id",
        )

    def list_evidence_gaps(self, hypothesis_id: str | None = None) -> list[EvidenceGap]:
        return self._list_payloads(
            "evidence_gaps",
            EvidenceGap,
            hypothesis_id=hypothesis_id,
            order_by="gap_id",
        )

    def list_review_decisions(
        self,
        hypothesis_id: str | None = None,
    ) -> list[HypothesisReviewDecision]:
        return self._list_payloads(
            "hypothesis_review_decisions",
            HypothesisReviewDecision,
            hypothesis_id=hypothesis_id,
            order_by="created_at, decision_id",
        )

    def list_lifecycle_events(
        self,
        hypothesis_id: str | None = None,
    ) -> list[HypothesisLifecycleEvent]:
        return self._list_payloads(
            "hypothesis_lifecycle_events",
            HypothesisLifecycleEvent,
            hypothesis_id=hypothesis_id,
            order_by="timestamp, event_id",
        )

    def list_generation_runs(self) -> list[HypothesisGenerationRun]:
        return self._list_payloads(
            "hypothesis_generation_runs",
            HypothesisGenerationRun,
            order_by="started_at, generation_run_id",
        )

    def export_hypotheses_json(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, list[dict[str, Any]]] = {}
        with self._connect() as connection:
            for table in TABLES:
                rows = connection.execute(
                    f"select payload_json from {table} order by rowid"
                ).fetchall()
                payload[table] = [json.loads(row["payload_json"]) for row in rows]
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def import_hypotheses_json(self, input_path: str | Path) -> None:
        payload = json.loads(Path(input_path).read_text())
        if not isinstance(payload, dict):
            raise ValueError("hypothesis import payload must be an object")
        hypotheses = [
            ResearchHypothesis.model_validate(item)
            for item in payload.get("research_hypotheses", [])
        ]
        questions = [
            TestableResearchQuestion.model_validate(item)
            for item in payload.get("testable_research_questions", [])
        ]
        criteria = [
            FalsificationCriterion.model_validate(item)
            for item in payload.get("falsification_criteria", [])
        ]
        gaps = [EvidenceGap.model_validate(item) for item in payload.get("evidence_gaps", [])]
        decisions = [
            HypothesisReviewDecision.model_validate(item)
            for item in payload.get("hypothesis_review_decisions", [])
        ]
        events = [
            HypothesisLifecycleEvent.model_validate(item)
            for item in payload.get("hypothesis_lifecycle_events", [])
        ]
        runs = [
            HypothesisGenerationRun.model_validate(item)
            for item in payload.get("hypothesis_generation_runs", [])
        ]
        with self._connect() as connection:
            for hypothesis in hypotheses:
                self._upsert_hypothesis(connection, hypothesis)
            for question in questions:
                _insert_payload(
                    connection,
                    "testable_research_questions",
                    "question_id",
                    question.question_id,
                    question.model_dump_json(),
                    {
                        "hypothesis_id": question.hypothesis_id,
                        "question_type": question.question_type,
                    },
                )
            for criterion in criteria:
                _insert_payload(
                    connection,
                    "falsification_criteria",
                    "criterion_id",
                    criterion.criterion_id,
                    criterion.model_dump_json(),
                    {
                        "hypothesis_id": criterion.hypothesis_id,
                        "evidence_type_needed": criterion.evidence_type_needed,
                    },
                )
            for gap in gaps:
                _insert_payload(
                    connection,
                    "evidence_gaps",
                    "gap_id",
                    gap.gap_id,
                    gap.model_dump_json(),
                    {
                        "hypothesis_id": gap.hypothesis_id,
                        "gap_type": gap.gap_type,
                        "severity": gap.severity,
                    },
                )
            for decision in decisions:
                _insert_payload(
                    connection,
                    "hypothesis_review_decisions",
                    "decision_id",
                    decision.decision_id,
                    decision.model_dump_json(),
                    {
                        "hypothesis_id": decision.hypothesis_id,
                        "reviewer_id": decision.reviewer_id,
                        "decision": decision.decision,
                        "created_at": _dt(decision.created_at),
                    },
                )
            for event in events:
                self._insert_lifecycle_event(connection, event)
            for run in runs:
                _insert_payload(
                    connection,
                    "hypothesis_generation_runs",
                    "generation_run_id",
                    run.generation_run_id,
                    run.model_dump_json(),
                    {
                        "project_id": run.project_id,
                        "program_id": run.program_id,
                        "graph_build_id": run.graph_build_id,
                        "started_at": _dt(run.started_at),
                        "completed_at": _dt(run.completed_at),
                    },
                )

    def retire_hypothesis(
        self,
        hypothesis_id: str,
        *,
        actor: str,
        reason: str,
    ) -> ResearchHypothesis:
        return self.update_hypothesis(
            hypothesis_id,
            {"status": "retired", "warnings": [reason]},
            actor,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return connection

    def _ensure_hypothesis(self, hypothesis_id: str) -> None:
        if not self._hypothesis_exists_id(hypothesis_id):
            raise ValueError(f"Unknown hypothesis: {hypothesis_id}")

    def _hypothesis_exists_id(self, hypothesis_id: str) -> bool:
        with self._connect() as connection:
            return self._hypothesis_exists(connection, hypothesis_id)

    def _hypothesis_exists(self, connection: sqlite3.Connection, hypothesis_id: str) -> bool:
        row = connection.execute(
            "select 1 from research_hypotheses where hypothesis_id = ?",
            (hypothesis_id,),
        ).fetchone()
        return row is not None

    def _upsert_hypothesis(
        self,
        connection: sqlite3.Connection,
        hypothesis: ResearchHypothesis,
    ) -> None:
        connection.execute(
            """
            insert or replace into research_hypotheses
            (
                hypothesis_id, hypothesis_type, status, project_id, program_id,
                created_at, updated_at, payload_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis.hypothesis_id,
                hypothesis.hypothesis_type,
                hypothesis.status,
                _metadata_text(hypothesis, "project_id"),
                _metadata_text(hypothesis, "program_id"),
                _dt(hypothesis.created_at),
                _dt(hypothesis.updated_at),
                hypothesis.model_dump_json(),
            ),
        )

    def _insert_lifecycle_event(
        self,
        connection: sqlite3.Connection,
        event: HypothesisLifecycleEvent,
    ) -> None:
        connection.execute(
            """
            insert or replace into hypothesis_lifecycle_events
            (event_id, hypothesis_id, event_type, actor, timestamp, payload_json)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.hypothesis_id,
                event.event_type,
                event.actor,
                _dt(event.timestamp),
                event.model_dump_json(),
            ),
        )

    def _list_payloads(
        self,
        table: str,
        model: type[Any],
        *,
        hypothesis_id: str | None = None,
        order_by: str,
    ) -> list[Any]:
        if table not in TABLES:
            raise ValueError(f"Unknown table: {table}")
        where = ""
        params: list[str] = []
        if hypothesis_id is not None:
            where = "where hypothesis_id = ?"
            params.append(hypothesis_id)
        adapter = TypeAdapter(model)
        with self._connect() as connection:
            rows = connection.execute(
                f"select payload_json from {table} {where} order by {order_by}",
                params,
            ).fetchall()
        return [adapter.validate_json(row["payload_json"]) for row in rows]


def _where_equals(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = []
    params = []
    for key, value in filters.items():
        if value is not None:
            clauses.append(f"{key} = ?")
            params.append(value)
    return (f"where {' and '.join(clauses)}" if clauses else "", params)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _metadata_text(hypothesis: ResearchHypothesis, key: str) -> str | None:
    value = hypothesis.metadata.get(key)
    return str(value) if value is not None else None


def _insert_payload(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    id_value: str,
    payload_json: str,
    columns: dict[str, Any],
) -> None:
    names = [id_column, *columns, "payload_json"]
    placeholders = ", ".join("?" for _ in names)
    values = [id_value, *columns.values(), payload_json]
    connection.execute(
        f"""
        insert or replace into {table}
        ({", ".join(names)})
        values ({placeholders})
        """,
        values,
    )
