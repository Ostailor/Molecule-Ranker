from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.agent_graph.schemas import AgentGraphRun, AgentNode


def append_audit_event(
    run: AgentGraphRun,
    *,
    event_type: str,
    message: str,
    node: AgentNode | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a deterministic, JSON-serializable graph audit event."""

    run.audit_events.append(
        {
            "event_type": event_type,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
            "graph_run_id": run.graph_run_id,
            "run_id": run.run_id,
            "node_id": node.node_id if node is not None else None,
            "agent_name": node.agent_name if node is not None else None,
            "metadata": metadata or {},
        }
    )
