from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask

FORBIDDEN_COMMAND_PATTERNS = (
    "rm -rf",
    "curl |",
    "curl -",
    "| sh",
    "| bash",
    "sudo",
    "chmod -r 777",
    "printenv",
    "cat .env",
    "git push",
    "gh auth token",
    "openai api key",
    "openai_api_key",
)

DEFAULT_ENGINEERING_ALLOWED_COMMANDS = (
    "git diff",
    "git status",
    "pytest",
    "ruff",
    "pyright",
    "molecule-ranker",
    "uv run pytest",
    "uv run ruff",
    "uv run pyright",
    "uv run molecule-ranker",
)

SECRET_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
)


@dataclass
class CodexBuiltCommand:
    command: list[str]
    prompt_via_stdin: bool = True
    prompt_file: Path | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class CodexRunnerResult:
    stdout: str
    stderr: str
    return_code: int | None
    timed_out: bool = False
    command: list[str] = field(default_factory=list)
    dry_run: bool = False


class CodexCommandBuilder:
    """Build safe, configurable Codex CLI commands without invoking a shell."""

    def build(
        self,
        task: CodexTask,
        config: CodexBackboneConfig,
        *,
        prompt_file: Path | None = None,
    ) -> CodexBuiltCommand:
        warnings = self.validate_task_commands(task, config)
        command = self._base_command(config)
        command.extend(self._metadata_list(task, "codex_pre_args"))
        self._append_model_args(command, task, config)

        exec_subcommand = str(task.metadata.get("codex_exec_subcommand", "exec"))
        include_exec = bool(task.metadata.get("codex_include_exec_subcommand", True))
        if include_exec and exec_subcommand:
            command.append(exec_subcommand)

        command.extend(self._metadata_list(task, "codex_exec_args"))
        prompt_mode = str(task.metadata.get("codex_prompt_mode", "stdin"))
        prompt_via_stdin = prompt_mode == "stdin"
        if prompt_mode in {"file", "prompt_file"}:
            if prompt_file is None:
                warnings.append("Prompt-file mode requested without a prompt file.")
            else:
                command.append(str(prompt_file))
            prompt_via_stdin = False
        elif prompt_mode == "argument":
            command.append(redact_secrets(task.prompt))
            prompt_via_stdin = False

        if config.codex_require_json or task.require_json or task.expected_output_format == "json":
            json_flag = str(task.metadata.get("codex_json_flag", "--json"))
            if json_flag and json_flag not in command:
                command.append(json_flag)
        command.extend(self._metadata_list(task, "codex_extra_args"))
        return CodexBuiltCommand(
            command=command,
            prompt_via_stdin=prompt_via_stdin,
            prompt_file=prompt_file,
            warnings=warnings,
        )

    def validate_task_commands(
        self,
        task: CodexTask,
        config: CodexBackboneConfig,
    ) -> list[str]:
        warnings: list[str] = []
        forbidden = [
            *FORBIDDEN_COMMAND_PATTERNS,
            *config.codex_forbidden_commands,
            *task.forbidden_commands,
        ]
        scanned_texts = [("prompt", task.prompt)]
        scanned_texts.extend(("allowed command", command) for command in task.allowed_commands)
        scanned_texts.extend(
            ("configured allowed command", command) for command in config.codex_allowed_commands
        )
        for label, text in scanned_texts:
            lowered = text.lower()
            for pattern in forbidden:
                if pattern and pattern.lower() in lowered:
                    warnings.append(f"Forbidden command pattern in {label}: {pattern}")
        allowed = self.allowed_commands(task, config)
        if config.codex_allow_shell_commands:
            for command in [*task.allowed_commands, *config.codex_allowed_commands]:
                if command and not self._is_allowed(command, allowed):
                    warnings.append(f"Command is not allowlisted: {command}")
        elif task.allowed_commands or config.codex_allowed_commands:
            warnings.append("Shell command execution is disabled; allowed commands are ignored.")
        return warnings

    def allowed_commands(self, task: CodexTask, config: CodexBackboneConfig) -> list[str]:
        if task.task_type in {"engineering_plan", "engineering_test_loop"}:
            return [
                *DEFAULT_ENGINEERING_ALLOWED_COMMANDS,
                *config.codex_allowed_commands,
                *task.allowed_commands,
            ]
        return [*config.codex_allowed_commands, *task.allowed_commands]

    def _base_command(self, config: CodexBackboneConfig) -> list[str]:
        command = shlex.split(config.codex_cli_command)
        return command or ["codex"]

    def _append_model_args(
        self,
        command: list[str],
        task: CodexTask,
        config: CodexBackboneConfig,
    ) -> None:
        model_flag = str(task.metadata.get("codex_model_flag", "--model"))
        reasoning_flag = str(task.metadata.get("codex_reasoning_flag", "--reasoning-effort"))
        non_interactive_flag = task.metadata.get("codex_non_interactive_flag")
        if config.codex_model and model_flag:
            command.extend([model_flag, config.codex_model])
        if config.codex_reasoning_effort and reasoning_flag:
            command.extend([reasoning_flag, config.codex_reasoning_effort])
        if isinstance(non_interactive_flag, str) and non_interactive_flag:
            command.append(non_interactive_flag)

    def _metadata_list(self, task: CodexTask, key: str) -> list[str]:
        raw = task.metadata.get(key, [])
        if isinstance(raw, str):
            return shlex.split(raw)
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return raw
        return []

    def _is_allowed(self, command: str, allowed: list[str]) -> bool:
        normalized = command.strip()
        return any(normalized == item or normalized.startswith(f"{item} ") for item in allowed)


class CodexCLIRunner:
    """Invoke Codex CLI safely from a configured working directory."""

    def __init__(self, command_builder: CodexCommandBuilder | None = None) -> None:
        self.command_builder = command_builder or CodexCommandBuilder()

    def build_command(self, task: CodexTask, config: CodexBackboneConfig) -> list[str]:
        return self.command_builder.build(task, config).command

    def run_task(
        self,
        task: CodexTask,
        config: CodexBackboneConfig,
        *,
        prompt: str,
        cwd: Path,
    ) -> CodexRunnerResult:
        prompt_file = self._write_prompt_file(task, config, prompt, cwd)
        built = self.command_builder.build(task, config, prompt_file=prompt_file)
        if built.warnings:
            return CodexRunnerResult(
                stdout="",
                stderr="\n".join(built.warnings),
                return_code=2,
                command=built.command,
            )
        if config.codex_dry_run:
            output = json.dumps(
                {
                    "dry_run": True,
                    "command": built.command,
                    "working_directory": str(cwd),
                    "prompt": redact_secrets(prompt),
                },
                indent=2,
                sort_keys=True,
            )
            self._store_transcript(
                task,
                config,
                cwd,
                prompt=prompt,
                command=built.command,
                stdout=output,
                stderr="",
            )
            return CodexRunnerResult(
                stdout=output,
                stderr="",
                return_code=0,
                command=built.command,
                dry_run=True,
            )
        result = self.run(
            built.command,
            prompt=prompt if built.prompt_via_stdin else "",
            cwd=cwd,
            timeout_seconds=task.timeout_seconds or config.codex_timeout_seconds,
            store_transcript=config.codex_store_transcripts,
            task=task,
            config=config,
        )
        if prompt_file is not None:
            prompt_file.unlink(missing_ok=True)
        return result

    def run(
        self,
        command: list[str],
        *,
        prompt: str,
        cwd: Path,
        timeout_seconds: int,
        store_transcript: bool = False,
        task: CodexTask | None = None,
        config: CodexBackboneConfig | None = None,
    ) -> CodexRunnerResult:
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                cwd=cwd,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
                env=_safe_subprocess_env(),
            )
        except FileNotFoundError:
            return CodexRunnerResult(
                stdout="",
                stderr=f"Codex CLI unavailable: command not found: {command[0]}",
                return_code=127,
                command=command,
            )
        except subprocess.TimeoutExpired as exc:
            result = CodexRunnerResult(
                stdout=redact_secrets(_to_text(exc.stdout)),
                stderr=redact_secrets(_to_text(exc.stderr)),
                return_code=None,
                timed_out=True,
                command=command,
            )
            if store_transcript and task is not None and config is not None:
                self._store_transcript(
                    task,
                    config,
                    cwd,
                    prompt=prompt,
                    command=command,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            return result
        result = CodexRunnerResult(
            stdout=redact_secrets(completed.stdout),
            stderr=redact_secrets(completed.stderr),
            return_code=completed.returncode,
            timed_out=False,
            command=command,
        )
        if store_transcript and task is not None and config is not None:
            self._store_transcript(
                task,
                config,
                cwd,
                prompt=prompt,
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def _write_prompt_file(
        self,
        task: CodexTask,
        config: CodexBackboneConfig,
        prompt: str,
        cwd: Path,
    ) -> Path | None:
        prompt_mode = str(task.metadata.get("codex_prompt_mode", "stdin"))
        if prompt_mode not in {"file", "prompt_file"}:
            return None
        directory = cwd / ".molecule-ranker" / "codex_backbone_prompts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{task.task_id}.prompt.txt"
        path.write_text(redact_secrets(prompt) if config.codex_redact_secrets else prompt)
        return path

    def _store_transcript(
        self,
        task: CodexTask,
        config: CodexBackboneConfig,
        cwd: Path,
        *,
        prompt: str,
        command: list[str],
        stdout: str,
        stderr: str,
    ) -> None:
        if not config.codex_store_transcripts:
            return
        directory = cwd / ".molecule-ranker" / "codex_backbone_transcripts"
        directory.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "command": command,
            "prompt": prompt,
            "stdout": stdout,
            "stderr": stderr,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if config.codex_redact_secrets:
            payload = {
                key: redact_secrets(value) if isinstance(value, str) else value
                for key, value in payload.items()
            }
        path = directory / f"{task.task_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _safe_subprocess_env() -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(marker in upper for marker in SECRET_ENV_MARKERS):
            continue
        safe[key] = value
    return safe


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


CodexRunner = CodexCLIRunner
