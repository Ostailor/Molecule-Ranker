from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molecule_ranker.review.audit import create_audit_event
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.queue_builder import build_review_workspace_from_artifact
from molecule_ranker.review.schemas import (
    CandidateDossier,
    FollowupRequest,
    ReviewAuditEvent,
    Reviewer,
    ReviewerComment,
    ReviewerDecision,
    ReviewItem,
    ReviewWorkspace,
    ValidationHandoff,
)
from molecule_ranker.review.validation_handoff import build_validation_handoff


def create_workspace_from_artifact(
    payload: dict[str, Any],
    *,
    reviewer: Reviewer | None = None,
    run_id: str | None = None,
) -> ReviewWorkspace:
    return build_review_workspace_from_artifact(payload, reviewer=reviewer, run_id=run_id)


def record_decision(
    workspace: ReviewWorkspace,
    *,
    review_item_id: str,
    reviewer: Reviewer,
    decision: str,
    rationale: str,
    confidence: float,
    decision_factors: list[str] | None = None,
):
    return ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=review_item_id,
        reviewer=reviewer,
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        decision_factors=decision_factors,
    )


def create_validation_handoff(
    workspace: ReviewWorkspace,
    *,
    review_item_id: str,
    evidence_packet_paths: dict[str, str] | None = None,
) -> ValidationHandoff:
    return build_validation_handoff(
        workspace,
        review_item_id,
        evidence_packet_paths=evidence_packet_paths,
    )


def _get_item(workspace: ReviewWorkspace, review_item_id: str):
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return item
    raise ValueError(f"Unknown review item: {review_item_id}")


class ReviewWorkspaceSummary(BaseModel):
    workspace_id: str
    run_id: str
    disease_name: str
    created_at: str
    review_item_count: int
    pending_count: int
    decision_count: int
    comment_count: int
    followup_request_count: int


class ReviewWorkspaceStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_workspace(self, workspace: ReviewWorkspace) -> ReviewWorkspace:
        event = create_audit_event(
            event_type="workspace_created",
            actor="ReviewWorkspaceStore",
            object_type="ReviewWorkspace",
            object_id=workspace.workspace_id,
            summary=f"Created review workspace for {workspace.disease_name}.",
            after={
                "workspace_id": workspace.workspace_id,
                "review_item_count": len(workspace.review_items),
            },
        )
        workspace.audit_events.append(event)
        with self._connect() as connection:
            self._upsert_workspace(connection, workspace)
            for item in workspace.review_items:
                self._upsert_review_item(connection, workspace.workspace_id, item)
            for decision in workspace.decisions:
                self._insert_decision(connection, workspace.workspace_id, decision)
            for comment in workspace.comments:
                self._insert_comment(connection, workspace.workspace_id, comment)
            for request in workspace.followup_requests:
                self._insert_followup_request(connection, workspace.workspace_id, request)
            for audit in workspace.audit_events:
                self._insert_audit_event(connection, workspace.workspace_id, audit)
        return workspace

    def get_workspace(self, workspace_id: str) -> ReviewWorkspace:
        with self._connect() as connection:
            row = connection.execute(
                """
                select workspace_id, run_id, disease_name, created_at, metadata_json
                from review_workspaces
                where workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown review workspace: {workspace_id}")
            items = [
                ReviewItem.model_validate_json(item_row["payload_json"])
                for item_row in connection.execute(
                    """
                    select payload_json from review_items
                    where workspace_id = ?
                    order by rowid
                    """,
                    (workspace_id,),
                )
            ]
            decisions = [
                ReviewerDecision.model_validate_json(decision_row["payload_json"])
                for decision_row in connection.execute(
                    """
                    select payload_json from reviewer_decisions
                    where workspace_id = ?
                    order by created_at, rowid
                    """,
                    (workspace_id,),
                )
            ]
            comments = [
                ReviewerComment.model_validate_json(comment_row["payload_json"])
                for comment_row in connection.execute(
                    """
                    select payload_json from reviewer_comments
                    where workspace_id = ?
                    order by created_at, rowid
                    """,
                    (workspace_id,),
                )
            ]
            requests = [
                FollowupRequest.model_validate_json(request_row["payload_json"])
                for request_row in connection.execute(
                    """
                    select payload_json from followup_requests
                    where workspace_id = ?
                    order by created_at, rowid
                    """,
                    (workspace_id,),
                )
            ]
            events = [
                ReviewAuditEvent.model_validate_json(event_row["payload_json"])
                for event_row in connection.execute(
                    """
                    select payload_json from audit_events
                    where workspace_id = ?
                    order by created_at, rowid
                    """,
                    (workspace_id,),
                )
            ]
        return ReviewWorkspace(
            workspace_id=str(row["workspace_id"]),
            run_id=str(row["run_id"]),
            disease_name=str(row["disease_name"]),
            created_at=_parse_datetime(str(row["created_at"])),
            review_items=items,
            decisions=decisions,
            comments=comments,
            followup_requests=requests,
            audit_events=events,
            metadata=json.loads(str(row["metadata_json"] or "{}")),
        )

    def list_workspaces(self) -> list[ReviewWorkspaceSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select
                    w.workspace_id,
                    w.run_id,
                    w.disease_name,
                    w.created_at,
                    count(distinct i.review_item_id) as review_item_count,
                    sum(case when i.review_status = 'pending' then 1 else 0 end) as pending_count,
                    count(distinct d.decision_id) as decision_count,
                    count(distinct c.comment_id) as comment_count,
                    count(distinct f.request_id) as followup_request_count
                from review_workspaces w
                left join review_items i on i.workspace_id = w.workspace_id
                left join reviewer_decisions d on d.workspace_id = w.workspace_id
                left join reviewer_comments c on c.workspace_id = w.workspace_id
                left join followup_requests f on f.workspace_id = w.workspace_id
                group by w.workspace_id
                order by w.created_at desc, w.workspace_id
                """
            ).fetchall()
        return [
            ReviewWorkspaceSummary(
                workspace_id=str(row["workspace_id"]),
                run_id=str(row["run_id"]),
                disease_name=str(row["disease_name"]),
                created_at=str(row["created_at"]),
                review_item_count=int(row["review_item_count"] or 0),
                pending_count=int(row["pending_count"] or 0),
                decision_count=int(row["decision_count"] or 0),
                comment_count=int(row["comment_count"] or 0),
                followup_request_count=int(row["followup_request_count"] or 0),
            )
            for row in rows
        ]

    def add_decision(self, workspace_id: str, decision: ReviewerDecision) -> None:
        with self._connect() as connection:
            self._require_workspace(connection, workspace_id)
            self._require_item(connection, workspace_id, decision.review_item_id)
            self._insert_decision(connection, workspace_id, decision)
            self._insert_audit_event(
                connection,
                workspace_id,
                create_audit_event(
                    event_type="decision_added",
                    actor=decision.reviewer,
                    object_type="ReviewerDecision",
                    object_id=decision.decision_id,
                    summary=f"Decision added: {decision.decision}.",
                    after=decision.model_dump(mode="json"),
                ),
            )

    def add_comment(self, workspace_id: str, comment: ReviewerComment) -> None:
        with self._connect() as connection:
            self._require_workspace(connection, workspace_id)
            self._require_item(connection, workspace_id, comment.review_item_id)
            self._insert_comment(connection, workspace_id, comment)
            self._insert_audit_event(
                connection,
                workspace_id,
                create_audit_event(
                    event_type="comment_added",
                    actor=comment.reviewer,
                    object_type="ReviewerComment",
                    object_id=comment.comment_id,
                    summary=f"Comment added: {comment.comment_type}.",
                    after=comment.model_dump(mode="json"),
                ),
            )

    def add_followup_request(self, workspace_id: str, request: FollowupRequest) -> None:
        with self._connect() as connection:
            self._require_workspace(connection, workspace_id)
            self._require_item(connection, workspace_id, request.review_item_id)
            self._insert_followup_request(connection, workspace_id, request)
            self._insert_audit_event(
                connection,
                workspace_id,
                create_audit_event(
                    event_type="followup_request_added",
                    actor=request.requested_by,
                    object_type="FollowupRequest",
                    object_id=request.request_id,
                    summary=f"Follow-up request added: {request.request_type}.",
                    after=request.model_dump(mode="json"),
                ),
            )

    def add_candidate_dossier(self, workspace_id: str, dossier: CandidateDossier) -> None:
        with self._connect() as connection:
            self._require_workspace(connection, workspace_id)
            self._require_item(connection, workspace_id, dossier.review_item_id)
            self._insert_candidate_dossier(connection, workspace_id, dossier)
            self._insert_audit_event(
                connection,
                workspace_id,
                create_audit_event(
                    event_type="candidate_dossier_added",
                    actor="ReviewWorkspaceStore",
                    object_type="CandidateDossier",
                    object_id=dossier.dossier_id,
                    summary=f"Candidate dossier added for {dossier.candidate_name}.",
                    after=dossier.model_dump(mode="json"),
                ),
            )

    def add_validation_handoff(self, workspace_id: str, handoff: ValidationHandoff) -> None:
        with self._connect() as connection:
            self._require_workspace(connection, workspace_id)
            self._require_item(connection, workspace_id, handoff.review_item_id)
            self._insert_validation_handoff(connection, workspace_id, handoff)
            self._insert_audit_event(
                connection,
                workspace_id,
                create_audit_event(
                    event_type="validation_handoff_added",
                    actor="ReviewWorkspaceStore",
                    object_type="ValidationHandoff",
                    object_id=handoff.handoff_id,
                    summary=f"Validation handoff added for {handoff.candidate_name}.",
                    after=handoff.model_dump(mode="json"),
                ),
            )

    def update_review_status(
        self,
        workspace_id: str,
        review_item_id: str,
        status: str,
        actor: str,
    ) -> None:
        with self._connect() as connection:
            self._require_workspace(connection, workspace_id)
            current = self._get_item(connection, workspace_id, review_item_id)
            updated = ReviewItem.model_validate(
                {
                    **current.model_dump(mode="json"),
                    "review_status": status,
                }
            )
            self._upsert_review_item(connection, workspace_id, updated)
            self._insert_audit_event(
                connection,
                workspace_id,
                create_audit_event(
                    event_type="review_status_updated",
                    actor=actor,
                    object_type="ReviewItem",
                    object_id=review_item_id,
                    summary=f"Review status updated to {status}.",
                    before={"review_status": current.review_status},
                    after={"review_status": updated.review_status},
                ),
            )

    def export_workspace_json(self, workspace_id: str, output_path: str | Path) -> Path:
        workspace = self.get_workspace(workspace_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(workspace.model_dump_json(indent=2) + "\n")
        return path

    def import_workspace_json(self, input_path: str | Path) -> ReviewWorkspace:
        path = Path(input_path)
        workspace = ReviewWorkspace.model_validate_json(path.read_text())
        event = create_audit_event(
            event_type="workspace_imported",
            actor="ReviewWorkspaceStore",
            object_type="ReviewWorkspace",
            object_id=workspace.workspace_id,
            summary=f"Imported review workspace from {path}.",
            metadata={"input_path": str(path)},
        )
        workspace.audit_events.append(event)
        with self._connect() as connection:
            self._upsert_workspace(connection, workspace)
            for item in workspace.review_items:
                self._upsert_review_item(connection, workspace.workspace_id, item)
            for decision in workspace.decisions:
                self._insert_decision(connection, workspace.workspace_id, decision)
            for comment in workspace.comments:
                self._insert_comment(connection, workspace.workspace_id, comment)
            for request in workspace.followup_requests:
                self._insert_followup_request(connection, workspace.workspace_id, request)
            for audit in workspace.audit_events:
                self._insert_audit_event(connection, workspace.workspace_id, audit)
        return workspace

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists review_workspaces (
                    workspace_id text primary key,
                    run_id text not null,
                    disease_name text not null,
                    created_at text not null,
                    metadata_json text not null,
                    payload_json text not null
                );

                create table if not exists review_items (
                    review_item_id text primary key,
                    workspace_id text not null,
                    run_id text not null,
                    disease_name text not null,
                    candidate_name text not null,
                    candidate_origin text not null,
                    review_status text not null,
                    priority_bucket text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create table if not exists reviewer_decisions (
                    decision_id text primary key,
                    workspace_id text not null,
                    review_item_id text not null,
                    reviewer_id text not null,
                    decision text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create table if not exists reviewer_comments (
                    comment_id text primary key,
                    workspace_id text not null,
                    review_item_id text not null,
                    reviewer_id text not null,
                    comment_type text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create table if not exists followup_requests (
                    request_id text primary key,
                    workspace_id text not null,
                    review_item_id text not null,
                    reviewer_id text not null,
                    request_type text not null,
                    priority text not null,
                    status text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create table if not exists candidate_dossiers (
                    dossier_id text primary key,
                    workspace_id text not null,
                    review_item_id text not null,
                    candidate_name text not null,
                    candidate_origin text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create table if not exists validation_handoffs (
                    handoff_id text primary key,
                    workspace_id text not null,
                    review_item_id text not null,
                    candidate_name text not null,
                    candidate_origin text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create table if not exists audit_events (
                    event_id text primary key,
                    workspace_id text not null,
                    event_type text not null,
                    actor text not null,
                    object_type text not null,
                    object_id text not null,
                    created_at text not null,
                    payload_json text not null,
                    foreign key (workspace_id) references review_workspaces(workspace_id)
                );

                create index if not exists idx_review_workspaces_run
                    on review_workspaces(run_id, disease_name);
                create index if not exists idx_review_items_search
                    on review_items(
                        workspace_id,
                        candidate_name,
                        candidate_origin,
                        review_status,
                        priority_bucket
                    );
                create index if not exists idx_reviewer_decisions_search
                    on reviewer_decisions(workspace_id, reviewer_id, decision, created_at);
                create index if not exists idx_reviewer_comments_search
                    on reviewer_comments(workspace_id, reviewer_id, comment_type, created_at);
                create index if not exists idx_followup_requests_search
                    on followup_requests(workspace_id, reviewer_id, status, priority, created_at);
                create index if not exists idx_audit_events_search
                    on audit_events(workspace_id, actor, event_type, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        return connection

    def _upsert_workspace(
        self,
        connection: sqlite3.Connection,
        workspace: ReviewWorkspace,
    ) -> None:
        connection.execute(
            """
            insert or replace into review_workspaces (
                workspace_id,
                run_id,
                disease_name,
                created_at,
                metadata_json,
                payload_json
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                workspace.workspace_id,
                workspace.run_id,
                workspace.disease_name,
                workspace.created_at.isoformat(),
                json.dumps(workspace.metadata, sort_keys=True),
                workspace.model_dump_json(),
            ),
        )

    def _upsert_review_item(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        item: ReviewItem,
    ) -> None:
        connection.execute(
            """
            insert or replace into review_items (
                review_item_id,
                workspace_id,
                run_id,
                disease_name,
                candidate_name,
                candidate_origin,
                review_status,
                priority_bucket,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.review_item_id,
                workspace_id,
                item.run_id,
                item.disease_name,
                item.candidate_name,
                item.candidate_origin,
                item.review_status,
                item.priority_bucket,
                self._workspace_created_at(connection, workspace_id),
                item.model_dump_json(),
            ),
        )

    def _insert_decision(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        decision: ReviewerDecision,
    ) -> None:
        connection.execute(
            """
            insert or ignore into reviewer_decisions (
                decision_id,
                workspace_id,
                review_item_id,
                reviewer_id,
                decision,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                workspace_id,
                decision.review_item_id,
                decision.reviewer.reviewer_id,
                decision.decision,
                decision.created_at.isoformat(),
                decision.model_dump_json(),
            ),
        )

    def _insert_comment(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        comment: ReviewerComment,
    ) -> None:
        connection.execute(
            """
            insert or ignore into reviewer_comments (
                comment_id,
                workspace_id,
                review_item_id,
                reviewer_id,
                comment_type,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comment.comment_id,
                workspace_id,
                comment.review_item_id,
                comment.reviewer.reviewer_id,
                comment.comment_type,
                comment.created_at.isoformat(),
                comment.model_dump_json(),
            ),
        )

    def _insert_followup_request(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        request: FollowupRequest,
    ) -> None:
        connection.execute(
            """
            insert or ignore into followup_requests (
                request_id,
                workspace_id,
                review_item_id,
                reviewer_id,
                request_type,
                priority,
                status,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.request_id,
                workspace_id,
                request.review_item_id,
                request.requested_by.reviewer_id,
                request.request_type,
                request.priority,
                request.status,
                request.created_at.isoformat(),
                request.model_dump_json(),
            ),
        )

    def _insert_candidate_dossier(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        dossier: CandidateDossier,
    ) -> None:
        connection.execute(
            """
            insert or replace into candidate_dossiers (
                dossier_id,
                workspace_id,
                review_item_id,
                candidate_name,
                candidate_origin,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dossier.dossier_id,
                workspace_id,
                dossier.review_item_id,
                dossier.candidate_name,
                dossier.candidate_origin,
                dossier.generated_at.isoformat(),
                dossier.model_dump_json(),
            ),
        )

    def _insert_validation_handoff(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        handoff: ValidationHandoff,
    ) -> None:
        connection.execute(
            """
            insert or replace into validation_handoffs (
                handoff_id,
                workspace_id,
                review_item_id,
                candidate_name,
                candidate_origin,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff.handoff_id,
                workspace_id,
                handoff.review_item_id,
                handoff.candidate_name,
                handoff.candidate_origin,
                handoff.created_at.isoformat(),
                handoff.model_dump_json(),
            ),
        )

    def _insert_audit_event(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        event: ReviewAuditEvent,
    ) -> None:
        connection.execute(
            """
            insert or ignore into audit_events (
                event_id,
                workspace_id,
                event_type,
                actor,
                object_type,
                object_id,
                created_at,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                workspace_id,
                event.event_type,
                event.actor,
                event.object_type,
                event.object_id,
                event.timestamp.isoformat(),
                event.model_dump_json(),
            ),
        )

    def _require_workspace(self, connection: sqlite3.Connection, workspace_id: str) -> None:
        exists = connection.execute(
            "select 1 from review_workspaces where workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if exists is None:
            raise ValueError(f"Unknown review workspace: {workspace_id}")

    def _require_item(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        review_item_id: str,
    ) -> None:
        self._get_item(connection, workspace_id, review_item_id)

    def _get_item(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        review_item_id: str,
    ) -> ReviewItem:
        row = connection.execute(
            """
            select payload_json from review_items
            where workspace_id = ? and review_item_id = ?
            """,
            (workspace_id, review_item_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown review item: {review_item_id}")
        return ReviewItem.model_validate_json(row["payload_json"])

    def _workspace_created_at(self, connection: sqlite3.Connection, workspace_id: str) -> str:
        row = connection.execute(
            "select created_at from review_workspaces where workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown review workspace: {workspace_id}")
        return str(row["created_at"])


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)
