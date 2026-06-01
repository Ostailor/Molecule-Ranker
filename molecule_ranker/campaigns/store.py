from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignExecutionEvent,
    CampaignExecutionEventType,
    CampaignMemo,
    CampaignPlan,
    CampaignWorkPackage,
    ReplanTrigger,
)

TABLES = (
    "campaigns",
    "campaign_objectives",
    "campaign_work_packages",
    "campaign_budgets",
    "campaign_plans",
    "campaign_execution_events",
    "replan_triggers",
    "campaign_memos",
    "campaign_stage_gates",
)


class CampaignStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.create_schema()

    def create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                create table if not exists campaigns (
                    campaign_id text primary key,
                    project_id text,
                    program_id text,
                    status text not null,
                    created_at text not null,
                    updated_at text not null,
                    payload_json text not null
                );

                create table if not exists campaign_objectives (
                    objective_id text primary key,
                    campaign_id text not null,
                    objective_type text not null,
                    payload_json text not null
                );

                create table if not exists campaign_work_packages (
                    work_package_id text primary key,
                    campaign_id text not null,
                    package_type text not null,
                    status text not null,
                    payload_json text not null
                );

                create table if not exists campaign_budgets (
                    budget_id text primary key,
                    campaign_id text not null,
                    payload_json text not null
                );

                create table if not exists campaign_plans (
                    campaign_plan_id text primary key,
                    campaign_id text not null,
                    created_at text not null,
                    payload_json text not null
                );

                create table if not exists campaign_execution_events (
                    event_id text primary key,
                    campaign_id text not null,
                    work_package_id text,
                    event_type text not null,
                    actor text,
                    timestamp text not null,
                    payload_json text not null
                );

                create table if not exists replan_triggers (
                    trigger_id text primary key,
                    campaign_id text not null,
                    trigger_type text not null,
                    severity text not null,
                    payload_json text not null
                );

                create table if not exists campaign_memos (
                    memo_id text primary key,
                    campaign_id text not null,
                    created_at text not null,
                    payload_json text not null
                );

                create table if not exists campaign_stage_gates (
                    gate_id text primary key,
                    campaign_id text not null,
                    work_package_id text,
                    gate_type text not null,
                    approval_status text not null,
                    payload_json text not null
                );

                create index if not exists idx_campaign_project on campaigns(project_id);
                create index if not exists idx_campaign_program on campaigns(program_id);
                create index if not exists idx_campaign_status on campaigns(status);
                create index if not exists idx_campaign_events
                    on campaign_execution_events(campaign_id, timestamp);
                """
            )

    def create_campaign(self, campaign: Campaign) -> Campaign:
        with self._connect() as connection:
            if self._exists(connection, "campaigns", "campaign_id", campaign.campaign_id):
                raise ValueError(f"Campaign already exists: {campaign.campaign_id}")
            self._upsert_campaign(connection, campaign)
            self._insert_event(
                connection,
                CampaignExecutionEvent(
                    event_id=_event_id(campaign.campaign_id, "created"),
                    campaign_id=campaign.campaign_id,
                    work_package_id=None,
                    event_type="created",
                    actor="CampaignStore",
                    summary=f"Created campaign {campaign.campaign_id}.",
                    before=None,
                    after=campaign.model_dump(mode="json"),
                    metadata={"audit_trail": True},
                ),
            )
        return campaign

    def get_campaign(self, campaign_id: str) -> Campaign:
        return Campaign.model_validate_json(
            self._payload("campaigns", "campaign_id", campaign_id)
        )

    def list_campaigns(
        self,
        *,
        project_id: str | None = None,
        program_id: str | None = None,
        status: str | None = None,
    ) -> list[Campaign]:
        where, params = _where_equals(
            {"project_id": project_id, "program_id": program_id, "status": status}
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                select payload_json from campaigns
                {where}
                order by updated_at desc, campaign_id
                """,
                params,
            ).fetchall()
        return [Campaign.model_validate_json(row["payload_json"]) for row in rows]

    def update_campaign_status(
        self,
        campaign_id: str,
        status: str,
        *,
        actor: str,
        rationale: str,
    ) -> Campaign:
        current = self.get_campaign(campaign_id)
        before = current.model_dump(mode="json")
        updated = Campaign.model_validate(
            {
                **before,
                "status": status,
                "updated_at": datetime.now(UTC),
            }
        )
        with self._connect() as connection:
            self._upsert_campaign(connection, updated)
            self._insert_event(
                connection,
                CampaignExecutionEvent(
                    event_id=_event_id(campaign_id, "status", status),
                    campaign_id=campaign_id,
                    work_package_id=None,
                    event_type=_campaign_status_event_type(status),
                    actor=actor,
                    summary=rationale,
                    before={"status": current.status},
                    after={"status": updated.status},
                    metadata={"audit_trail": True},
                ),
            )
        return updated

    def save_campaign_plan(self, plan: CampaignPlan) -> CampaignPlan:
        self._ensure_campaign(plan.campaign_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into campaign_plans
                (campaign_plan_id, campaign_id, created_at, payload_json)
                values (?, ?, ?, ?)
                """,
                (
                    plan.campaign_plan_id,
                    plan.campaign_id,
                    plan.created_at.isoformat(),
                    plan.model_dump_json(),
                ),
            )
            connection.execute(
                """
                insert or replace into campaign_budgets
                (budget_id, campaign_id, payload_json)
                values (?, ?, ?)
                """,
                (plan.budget.budget_id, plan.campaign_id, plan.budget.model_dump_json()),
            )
            for objective in plan.objectives:
                connection.execute(
                    """
                    insert or replace into campaign_objectives
                    (objective_id, campaign_id, objective_type, payload_json)
                    values (?, ?, ?, ?)
                    """,
                    (
                        objective.objective_id,
                        objective.campaign_id,
                        objective.objective_type,
                        objective.model_dump_json(),
                    ),
                )
            for package in plan.work_packages:
                self._upsert_work_package(connection, package)
        return plan

    def get_campaign_plan(self, campaign_plan_id: str) -> CampaignPlan:
        return CampaignPlan.model_validate_json(
            self._payload("campaign_plans", "campaign_plan_id", campaign_plan_id)
        )

    def get_latest_campaign_plan(self, campaign_id: str) -> CampaignPlan:
        self._ensure_campaign(campaign_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                select payload_json from campaign_plans
                where campaign_id = ?
                order by created_at desc, campaign_plan_id desc
                limit 1
                """,
                (campaign_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"No campaign plan exists for campaign: {campaign_id}")
        return CampaignPlan.model_validate_json(row["payload_json"])

    def get_work_package(self, work_package_id: str) -> CampaignWorkPackage:
        return CampaignWorkPackage.model_validate_json(
            self._payload("campaign_work_packages", "work_package_id", work_package_id)
        )

    def get_stage_gate(self, stage_gate_id: str) -> dict[str, Any]:
        return json.loads(
            self._payload("campaign_stage_gates", "gate_id", stage_gate_id)
        )

    def list_stage_gates(self, campaign_id: str) -> list[dict[str, Any]]:
        return self._list_payloads("campaign_stage_gates", campaign_id)

    def add_work_package(self, work_package: CampaignWorkPackage) -> CampaignWorkPackage:
        self._ensure_campaign(work_package.campaign_id)
        with self._connect() as connection:
            self._upsert_work_package(connection, work_package)
        return work_package

    def update_work_package_status(
        self,
        work_package_id: str,
        status: str,
        *,
        actor: str,
        rationale: str,
    ) -> CampaignWorkPackage:
        current = CampaignWorkPackage.model_validate_json(
            self._payload("campaign_work_packages", "work_package_id", work_package_id)
        )
        before = current.model_dump(mode="json")
        updated = CampaignWorkPackage.model_validate({**before, "status": status})
        with self._connect() as connection:
            self._upsert_work_package(connection, updated)
            self._insert_event(
                connection,
                CampaignExecutionEvent(
                    event_id=_event_id(updated.campaign_id, work_package_id, status),
                    campaign_id=updated.campaign_id,
                    work_package_id=work_package_id,
                    event_type=_work_package_status_event_type(status),
                    actor=actor,
                    summary=rationale,
                    before={"status": current.status},
                    after={"status": updated.status},
                    metadata={"audit_trail": True},
                ),
            )
        return updated

    def add_execution_event(self, event: CampaignExecutionEvent) -> CampaignExecutionEvent:
        self._ensure_campaign(event.campaign_id)
        with self._connect() as connection:
            self._insert_event(connection, event)
        return event

    def list_execution_events(self, campaign_id: str) -> list[CampaignExecutionEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select payload_json from campaign_execution_events
                where campaign_id = ?
                order by timestamp, event_id
                """,
                (campaign_id,),
            ).fetchall()
        return [CampaignExecutionEvent.model_validate_json(row["payload_json"]) for row in rows]

    def add_replan_trigger(self, trigger: ReplanTrigger) -> ReplanTrigger:
        self._ensure_campaign(trigger.campaign_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into replan_triggers
                (trigger_id, campaign_id, trigger_type, severity, payload_json)
                values (?, ?, ?, ?, ?)
                """,
                (
                    trigger.trigger_id,
                    trigger.campaign_id,
                    trigger.trigger_type,
                    trigger.severity,
                    trigger.model_dump_json(),
                ),
            )
        return trigger

    def list_replan_triggers(self, campaign_id: str) -> list[ReplanTrigger]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select payload_json from replan_triggers
                where campaign_id = ?
                order by trigger_id
                """,
                (campaign_id,),
            ).fetchall()
        return [ReplanTrigger.model_validate_json(row["payload_json"]) for row in rows]

    def add_stage_gate_decision(self, gate: dict[str, Any]) -> dict[str, Any]:
        campaign_id = str(gate["campaign_id"])
        self._ensure_campaign(campaign_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into campaign_stage_gates
                (gate_id, campaign_id, work_package_id, gate_type, approval_status, payload_json)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(gate["gate_id"]),
                    campaign_id,
                    gate.get("work_package_id"),
                    str(gate["gate_type"]),
                    str(gate["approval_status"]),
                    json.dumps(gate, sort_keys=True),
                ),
            )
            self._insert_event(
                connection,
                CampaignExecutionEvent(
                    event_id=_event_id(campaign_id, str(gate["gate_id"]), "stage_gate"),
                    campaign_id=campaign_id,
                    work_package_id=gate.get("work_package_id"),
                    event_type="stage_gate_decided",
                    actor="CampaignStore",
                    summary=f"Recorded stage gate {gate['gate_id']}.",
                    before=None,
                    after=gate,
                    metadata={"audit_trail": True},
                ),
            )
        return gate

    def save_campaign_memo(self, memo: CampaignMemo) -> CampaignMemo:
        self._ensure_campaign(memo.campaign_id)
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into campaign_memos
                (memo_id, campaign_id, created_at, payload_json)
                values (?, ?, ?, ?)
                """,
                (
                    memo.memo_id,
                    memo.campaign_id,
                    memo.created_at.isoformat(),
                    memo.model_dump_json(),
                ),
            )
        return memo

    def list_campaign_memos(self, campaign_id: str) -> list[CampaignMemo]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select payload_json from campaign_memos
                where campaign_id = ?
                order by created_at, memo_id
                """,
                (campaign_id,),
            ).fetchall()
        return [CampaignMemo.model_validate_json(row["payload_json"]) for row in rows]

    def export_campaign_json(self, campaign_id: str, output_path: str | Path) -> Path:
        payload = self._export_payload(campaign_id)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return target

    def import_campaign_json(self, input_path: str | Path) -> Campaign:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        campaign = Campaign.model_validate(payload["campaign"])
        with self._connect() as connection:
            self._upsert_campaign(connection, campaign)
            for plan in payload.get("campaign_plans", []):
                parsed_plan = CampaignPlan.model_validate(plan)
                connection.execute(
                    """
                    insert or replace into campaign_plans
                    (campaign_plan_id, campaign_id, created_at, payload_json)
                    values (?, ?, ?, ?)
                    """,
                    (
                        parsed_plan.campaign_plan_id,
                        parsed_plan.campaign_id,
                        parsed_plan.created_at.isoformat(),
                        parsed_plan.model_dump_json(),
                    ),
                )
            for package in payload.get("campaign_work_packages", []):
                self._upsert_work_package(connection, CampaignWorkPackage.model_validate(package))
            for event in payload.get("campaign_execution_events", []):
                self._insert_event(connection, CampaignExecutionEvent.model_validate(event))
            for trigger in payload.get("replan_triggers", []):
                parsed_trigger = ReplanTrigger.model_validate(trigger)
                connection.execute(
                    """
                    insert or replace into replan_triggers
                    (trigger_id, campaign_id, trigger_type, severity, payload_json)
                    values (?, ?, ?, ?, ?)
                    """,
                    (
                        parsed_trigger.trigger_id,
                        parsed_trigger.campaign_id,
                        parsed_trigger.trigger_type,
                        parsed_trigger.severity,
                        parsed_trigger.model_dump_json(),
                    ),
                )
            for memo in payload.get("campaign_memos", []):
                parsed_memo = CampaignMemo.model_validate(memo)
                connection.execute(
                    """
                    insert or replace into campaign_memos
                    (memo_id, campaign_id, created_at, payload_json)
                    values (?, ?, ?, ?)
                    """,
                    (
                        parsed_memo.memo_id,
                        parsed_memo.campaign_id,
                        parsed_memo.created_at.isoformat(),
                        parsed_memo.model_dump_json(),
                    ),
                )
            for gate in payload.get("campaign_stage_gates", []):
                connection.execute(
                    """
                    insert or replace into campaign_stage_gates
                    (
                        gate_id,
                        campaign_id,
                        work_package_id,
                        gate_type,
                        approval_status,
                        payload_json
                    )
                    values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(gate["gate_id"]),
                        str(gate["campaign_id"]),
                        gate.get("work_package_id"),
                        str(gate["gate_type"]),
                        str(gate["approval_status"]),
                        json.dumps(gate, sort_keys=True),
                    ),
                )
        return campaign

    def _export_payload(self, campaign_id: str) -> dict[str, Any]:
        self._ensure_campaign(campaign_id)
        return {
            "campaign": self.get_campaign(campaign_id).model_dump(mode="json"),
            "campaign_plans": self._list_payloads("campaign_plans", campaign_id),
            "campaign_work_packages": self._list_payloads(
                "campaign_work_packages", campaign_id
            ),
            "campaign_execution_events": [
                event.model_dump(mode="json") for event in self.list_execution_events(campaign_id)
            ],
            "replan_triggers": [
                trigger.model_dump(mode="json")
                for trigger in self.list_replan_triggers(campaign_id)
            ],
            "campaign_memos": [
                memo.model_dump(mode="json") for memo in self.list_campaign_memos(campaign_id)
            ],
            "campaign_stage_gates": self._list_payloads("campaign_stage_gates", campaign_id),
            "export_metadata": {
                "delete_default": "not_supported",
                "cancel_or_retire_instead": True,
                "audit_trail_preserved": True,
            },
        }

    def _list_payloads(self, table: str, campaign_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                f"select payload_json from {table} where campaign_id = ? order by 1",
                (campaign_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _payload(self, table: str, key_column: str, key: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                f"select payload_json from {table} where {key_column} = ?",
                (key,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown {table} record: {key}")
        return str(row["payload_json"])

    def _ensure_campaign(self, campaign_id: str) -> None:
        self.get_campaign(campaign_id)

    def _upsert_campaign(self, connection: sqlite3.Connection, campaign: Campaign) -> None:
        connection.execute(
            """
            insert or replace into campaigns
            (campaign_id, project_id, program_id, status, created_at, updated_at, payload_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign.campaign_id,
                campaign.project_id,
                campaign.program_id,
                campaign.status,
                campaign.created_at.isoformat(),
                campaign.updated_at.isoformat(),
                campaign.model_dump_json(),
            ),
        )

    def _upsert_work_package(
        self,
        connection: sqlite3.Connection,
        work_package: CampaignWorkPackage,
    ) -> None:
        connection.execute(
            """
            insert or replace into campaign_work_packages
            (work_package_id, campaign_id, package_type, status, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            (
                work_package.work_package_id,
                work_package.campaign_id,
                work_package.package_type,
                work_package.status,
                work_package.model_dump_json(),
            ),
        )

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        event: CampaignExecutionEvent,
    ) -> None:
        connection.execute(
            """
            insert or replace into campaign_execution_events
            (event_id, campaign_id, work_package_id, event_type, actor, timestamp, payload_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.campaign_id,
                event.work_package_id,
                event.event_type,
                event.actor,
                event.timestamp.isoformat(),
                event.model_dump_json(),
            ),
        )

    def _exists(
        self,
        connection: sqlite3.Connection,
        table: str,
        key_column: str,
        key: str,
    ) -> bool:
        row = connection.execute(
            f"select 1 from {table} where {key_column} = ?",
            (key,),
        ).fetchone()
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def _where_equals(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = []
    params = []
    for key, value in filters.items():
        if value is None:
            continue
        clauses.append(f"{key} = ?")
        params.append(value)
    if not clauses:
        return "", []
    return "where " + " and ".join(clauses), params


def _campaign_status_event_type(status: str) -> CampaignExecutionEventType:
    if status == "active":
        return "started"
    if status == "completed":
        return "completed"
    if status == "paused":
        return "paused"
    if status == "cancelled":
        return "cancelled"
    if status == "replanning_required":
        return "replanning_triggered"
    return "review_decision_added"


def _work_package_status_event_type(status: str) -> CampaignExecutionEventType:
    if status in {"ready", "approved"}:
        return "approved"
    if status == "in_progress":
        return "started"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status == "blocked":
        return "blocked"
    if status == "cancelled":
        return "cancelled"
    return "review_decision_added"


def _event_id(campaign_id: str, *parts: str) -> str:
    raw = "|".join([campaign_id, *parts, datetime.now(UTC).isoformat()])
    return f"campaign-event:{abs(hash(raw))}"


__all__ = ["CampaignStore", "TABLES"]
