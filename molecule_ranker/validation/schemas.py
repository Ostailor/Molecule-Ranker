from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

GoldenWorkflowMode = Literal["test", "live"]
GoldenWorkflowStatus = Literal["pass", "fail"]


class GoldenWorkflow(BaseModel):
    workflow_id: str
    name: str
    description: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_artifacts: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ForbiddenOutputFinding(BaseModel):
    artifact_path: Path
    phrase: str
    excerpt: str


class GoldenWorkflowResult(BaseModel):
    workflow_id: str
    name: str
    status: GoldenWorkflowStatus
    mode: GoldenWorkflowMode
    artifact_dir: Path
    artifacts: list[Path]
    missing_artifacts: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    forbidden_findings: list[ForbiddenOutputFinding] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoldenValidationReport(BaseModel):
    status: GoldenWorkflowStatus
    workflow_count: int
    live_validation: bool
    output_dir: Path
    results: list[GoldenWorkflowResult]
    metadata: dict[str, Any] = Field(default_factory=dict)
