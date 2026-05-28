from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from molecule_ranker.codex_backbone.audit import CodexAuditLogger
from molecule_ranker.codex_backbone.guardrails import (
    check_output,
    collect_allowed_refs_from_artifacts,
    has_blocking_task_guardrail,
    redact_secrets,
    task_guardrail_warnings,
)
from molecule_ranker.codex_backbone.parser import observe_commands, parse_codex_json
from molecule_ranker.codex_backbone.prompts import build_codex_prompt
from molecule_ranker.codex_backbone.runner import CodexRunner, CodexRunnerResult
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask, CodexTaskResult
from molecule_ranker.codex_backbone.usage import extract_usage_summary


class RunnerProtocol(Protocol):
    def build_command(self, task: CodexTask, config: CodexBackboneConfig) -> list[str]: ...

    def run(
        self,
        command: list[str],
        *,
        prompt: str,
        cwd: Path,
        timeout_seconds: int,
    ) -> CodexRunnerResult: ...


class NullCodexProvider:
    """Deterministic Codex provider for default validation and tests."""

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        now = datetime.now(UTC)
        output_json = {
            "provider": "NullCodexProvider",
            "task_id": task.task_id,
            "task_type": task.task_type,
            "artifact_refs": list(task.input_artifact_paths),
            "creates_evidence_items": False,
            "message": "Deterministic validation output; no live Codex or OpenAI API used.",
        }
        output_text = json.dumps(output_json, indent=2, sort_keys=True)
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status="succeeded",
            output_text=output_text,
            output_json=output_json,
            stdout=output_text,
            stderr="",
            return_code=0,
            artifacts_read=list(task.input_artifact_paths),
            artifacts_written=[],
            commands_observed=[],
            guardrail_warnings=[],
            usage_summary={"provider": "NullCodexProvider"},
            started_at=now,
            completed_at=now,
            metadata={"provider": "NullCodexProvider", "live_validation": False},
        )


class CodexBackboneProvider:
    """Primary LLM provider implementation backed by authenticated Codex CLI."""

    def __init__(
        self,
        config: CodexBackboneConfig | None = None,
        *,
        runner: RunnerProtocol | None = None,
    ) -> None:
        self.config = config or CodexBackboneConfig()
        self.runner = runner or CodexRunner()

    def run_task(self, task: CodexTask) -> CodexTaskResult:
        started_at = datetime.now(UTC)
        cwd = self._working_directory(task)
        command = self.runner.build_command(task, self.config)

        if not self.config.enable_codex_backbone:
            result = self._result(
                task,
                status="disabled",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                stderr="Codex backbone is disabled. Set enable_codex_backbone=True to opt in.",
            )
            self._audit(task, result, prompt_text="", command=command, cwd=cwd)
            return result

        warnings = (
            task_guardrail_warnings(task, self.config)
            if self.config.codex_guardrails_enabled
            else []
        )
        if self.config.codex_guardrails_enabled and has_blocking_task_guardrail(warnings):
            result = self._result(
                task,
                status="guardrail_failed",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                guardrail_warnings=warnings,
            )
            self._audit(task, result, prompt_text="", command=command, cwd=cwd)
            return result

        prompt_bundle = build_codex_prompt(task, self.config)
        warnings.extend(prompt_bundle.guardrail_warnings)
        prompt_text = prompt_bundle.prompt_text
        if self.config.codex_dry_run:
            output = json.dumps(
                {
                    "dry_run": True,
                    "command": command,
                    "working_directory": str(cwd),
                    "prompt": prompt_text,
                },
                indent=2,
                sort_keys=True,
            )
            result = self._result(
                task,
                status="succeeded",
                output_text=output,
                output_json={"dry_run": True, "command": command, "prompt": prompt_text},
                stdout=output,
                artifacts_read=prompt_bundle.artifacts_read,
                guardrail_warnings=warnings,
                commands_observed=[" ".join(command)],
                started_at=started_at,
                completed_at=datetime.now(UTC),
                metadata={"dry_run": True},
            )
            self._audit(task, result, prompt_text=prompt_text, command=command, cwd=cwd)
            return result

        runner_result = self.runner.run(
            command,
            prompt=prompt_text,
            cwd=cwd,
            timeout_seconds=task.timeout_seconds or self.config.codex_timeout_seconds,
        )
        completed_at = datetime.now(UTC)
        output_text = runner_result.stdout
        if runner_result.timed_out:
            result = self._result(
                task,
                status="timed_out",
                output_text=output_text,
                stdout=runner_result.stdout,
                stderr=runner_result.stderr,
                return_code=runner_result.return_code,
                artifacts_read=prompt_bundle.artifacts_read,
                guardrail_warnings=warnings,
                commands_observed=observe_commands(output_text),
                usage_summary=extract_usage_summary(runner_result.stdout, runner_result.stderr),
                started_at=started_at,
                completed_at=completed_at,
            )
            result = self._apply_output_guardrails(task, result)
            self._audit(task, result, prompt_text=prompt_text, command=command, cwd=cwd)
            return result
        if warnings and self.config.codex_guardrails_enabled:
            result = self._result(
                task,
                status="guardrail_failed",
                output_text=output_text,
                stdout=runner_result.stdout,
                stderr=runner_result.stderr,
                return_code=runner_result.return_code,
                artifacts_read=prompt_bundle.artifacts_read,
                guardrail_warnings=warnings,
                commands_observed=observe_commands(output_text),
                usage_summary=extract_usage_summary(runner_result.stdout, runner_result.stderr),
                started_at=started_at,
                completed_at=completed_at,
            )
            self._audit(task, result, prompt_text=prompt_text, command=command, cwd=cwd)
            return result

        if runner_result.return_code not in {0, None}:
            result = self._result(
                task,
                status="failed",
                output_text=output_text,
                stdout=runner_result.stdout,
                stderr=runner_result.stderr,
                return_code=runner_result.return_code,
                artifacts_read=prompt_bundle.artifacts_read,
                commands_observed=observe_commands(output_text),
                usage_summary=extract_usage_summary(runner_result.stdout, runner_result.stderr),
                started_at=started_at,
                completed_at=completed_at,
            )
            result = self._apply_output_guardrails(task, result)
            self._audit(task, result, prompt_text=prompt_text, command=command, cwd=cwd)
            return result

        output_json = None
        if task.require_json or self.config.codex_require_json:
            try:
                output_json = parse_codex_json(output_text)
            except ValueError as exc:
                result = self._result(
                    task,
                    status="parse_failed",
                    output_text=output_text,
                    stdout=runner_result.stdout,
                    stderr=f"{runner_result.stderr}\n{exc}".strip(),
                    return_code=runner_result.return_code,
                    artifacts_read=prompt_bundle.artifacts_read,
                    commands_observed=observe_commands(output_text),
                    usage_summary=extract_usage_summary(
                        runner_result.stdout,
                        runner_result.stderr,
                    ),
                    started_at=started_at,
                    completed_at=completed_at,
                )
                result = self._apply_output_guardrails(task, result)
                self._audit(task, result, prompt_text=prompt_text, command=command, cwd=cwd)
                return result

        result = self._result(
            task,
            status="succeeded" if runner_result.return_code == 0 else "failed",
            output_text=output_text,
            output_json=output_json,
            stdout=runner_result.stdout,
            stderr=runner_result.stderr,
            return_code=runner_result.return_code,
            artifacts_read=prompt_bundle.artifacts_read,
            commands_observed=observe_commands(output_text),
            usage_summary=extract_usage_summary(runner_result.stdout, runner_result.stderr),
            started_at=started_at,
            completed_at=completed_at,
        )
        result = self._apply_output_guardrails(task, result)
        self._audit(task, result, prompt_text=prompt_text, command=command, cwd=cwd)
        return result

    def _working_directory(self, task: CodexTask) -> Path:
        return Path(self.config.codex_working_dir or task.working_directory).resolve()

    def _audit(
        self,
        task: CodexTask,
        result: CodexTaskResult,
        *,
        prompt_text: str,
        command: list[str],
        cwd: Path,
    ) -> None:
        if self.config.codex_redact_secrets:
            result.output_text = redact_secrets(result.output_text)
            result.stdout = redact_secrets(result.stdout)
            result.stderr = redact_secrets(result.stderr)
        CodexAuditLogger(cwd).write(
            task,
            result,
            prompt_text=prompt_text,
            command=command,
            config=self.config,
        )

    def _apply_output_guardrails(
        self,
        task: CodexTask,
        result: CodexTaskResult,
    ) -> CodexTaskResult:
        if not self.config.codex_guardrails_enabled:
            return result
        artifact_refs, citation_ids = collect_allowed_refs_from_artifacts(
            task.input_artifact_paths
        )
        return check_output(result, artifact_refs, citation_ids)

    def _result(
        self,
        task: CodexTask,
        *,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        output_text: str = "",
        output_json: dict[str, object] | None = None,
        stdout: str = "",
        stderr: str = "",
        return_code: int | None = None,
        artifacts_read: list[str] | None = None,
        artifacts_written: list[str] | None = None,
        commands_observed: list[str] | None = None,
        guardrail_warnings: list[str] | None = None,
        usage_summary: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> CodexTaskResult:
        return CodexTaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            status=status,  # type: ignore[arg-type]
            output_text=output_text,
            output_json=output_json,  # type: ignore[arg-type]
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            artifacts_read=artifacts_read or [],
            artifacts_written=artifacts_written or [],
            commands_observed=commands_observed or [],
            guardrail_warnings=guardrail_warnings or [],
            usage_summary=usage_summary or {},
            started_at=started_at,
            completed_at=completed_at,
            metadata=metadata or {},
        )
