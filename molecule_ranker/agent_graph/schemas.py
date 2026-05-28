from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentNodeStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
AgentGraphStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]


class AgentNode(BaseModel):
    node_id: str
    agent_name: str
    agent_type: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    optional_artifacts: list[str] = Field(default_factory=list)
    status: AgentNodeStatus = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEdge(BaseModel):
    from_node_id: str
    to_node_id: str
    artifact_key: str
    required: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentGraphRun(BaseModel):
    graph_run_id: str
    project_id: str | None = None
    run_id: str
    graph_version: str
    nodes: list[AgentNode] = Field(default_factory=list)
    edges: list[AgentEdge] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    audit_events: list[dict[str, Any]] = Field(default_factory=list)
    status: AgentGraphStatus = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)
