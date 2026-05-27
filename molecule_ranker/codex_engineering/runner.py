from __future__ import annotations

from pathlib import Path

from molecule_ranker.codex_backbone.provider import CodexBackboneProvider
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask, CodexTaskResult
from molecule_ranker.codex_engineering.task_builder import validate_engineering_task


class CodexEngineeringRunner:
    def __init__(
        self,
        *,
        codex_command: str = "codex",
        working_directory: Path = Path("."),
    ) -> None:
        self.codex_command = codex_command
        self.working_directory = working_directory.resolve()

    def run(
        self,
        task: CodexTask,
        *,
        apply: bool = False,
        allow_git_push: bool = False,
        allow_deletions: bool = False,
    ) -> CodexTaskResult:
        warnings = validate_engineering_task(task)
        if warnings:
            return _failed_task_result(task, warnings)
        config = CodexBackboneConfig(
            enable_codex_backbone=True,
            codex_cli_command=self.codex_command,
            codex_working_dir=self.working_directory,
            codex_dry_run=not apply,
            codex_allow_shell_commands=True,
            codex_allowed_commands=list(task.allowed_commands),
            codex_forbidden_commands=list(task.forbidden_commands),
            codex_store_transcripts=True,
        )
        if not allow_git_push and "git push" not in config.codex_forbidden_commands:
            config.codex_forbidden_commands.append("git push")
        if not allow_deletions:
            for command in ["rm ", "rm -r", "rm -rf", "git rm"]:
                if command not in config.codex_forbidden_commands:
                    config.codex_forbidden_commands.append(command)
        return CodexBackboneProvider(config).run_task(task)


def _failed_task_result(task: CodexTask, warnings: list[str]) -> CodexTaskResult:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return CodexTaskResult(
        task_id=task.task_id,
        task_type=task.task_type,
        status="guardrail_failed",
        stderr="\n".join(warnings),
        guardrail_warnings=warnings,
        started_at=now,
        completed_at=now,
    )
