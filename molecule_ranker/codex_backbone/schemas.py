from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

CodexTaskType = Literal[
    "summarize_run",
    "explain_ranking",
    "compare_candidates",
    "compare_runs",
    "summarize_project",
    "explain_run_changes",
    "draft_project_update",
    "suggest_next_project_actions",
    "draft_dossier",
    "generate_review_questions",
    "explain_conflicting_evidence",
    "summarize_experimental_results",
    "explain_active_learning",
    "plan_followup_run",
    "inspect_artifacts",
    "engineering_plan",
    "engineering_test_loop",
    "suggest_schema_mapping",
    "explain_sync_failure",
    "summarize_external_record",
    "suggest_mapping_review_questions",
    "draft_export_summary",
    "compare_internal_external_record",
    "summarize_model_card",
    "explain_model_metrics",
    "explain_prediction_batch",
    "suggest_feature_debugging",
    "draft_model_limitations",
    "explain_active_design_model_influence",
    "suggest_structure_selection_review_questions",
    "summarize_structure_assessment",
    "explain_pose_qc_failure",
    "draft_structure_report_summary",
    "plan_followup_structure_workflow",
]
CodexOutputFormat = Literal["json", "markdown", "text"]
CodexResultStatus = Literal[
    "succeeded",
    "failed",
    "timed_out",
    "guardrail_failed",
    "parse_failed",
    "disabled",
]


class CodexTask(BaseModel):
    task_id: str
    task_type: CodexTaskType
    prompt: str
    working_directory: str
    input_artifact_paths: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    forbidden_commands: list[str] = Field(default_factory=list)
    expected_output_format: CodexOutputFormat = "json"
    timeout_seconds: int = Field(default=300, gt=0)
    require_json: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "prompt", "working_directory")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class CodexTaskResult(BaseModel):
    task_id: str
    task_type: CodexTaskType
    status: CodexResultStatus
    output_text: str = ""
    output_json: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = None
    artifacts_read: list[str] = Field(default_factory=list)
    artifacts_written: list[str] = Field(default_factory=list)
    commands_observed: list[str] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)
    usage_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodexBackboneConfig(BaseModel):
    enable_codex_backbone: bool = False
    codex_cli_command: str = "codex"
    codex_model: str | None = None
    codex_reasoning_effort: str | None = "high"
    codex_working_dir: Path | None = None
    codex_timeout_seconds: int = Field(default=300, gt=0)
    codex_require_json: bool = True
    codex_dry_run: bool = False
    codex_allow_shell_commands: bool = False
    codex_allowed_commands: list[str] = Field(default_factory=list)
    codex_forbidden_commands: list[str] = Field(default_factory=list)
    codex_max_artifact_bytes: int = Field(default=1_000_000, gt=0)
    codex_redact_secrets: bool = True
    codex_store_transcripts: bool = True
    codex_guardrails_enabled: bool = True
