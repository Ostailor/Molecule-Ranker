from __future__ import annotations

import hashlib
import json
from pathlib import Path

from molecule_ranker.codex_backbone.runner import DEFAULT_ENGINEERING_ALLOWED_COMMANDS
from molecule_ranker.codex_backbone.schemas import CodexTask
from molecule_ranker.codex_engineering.prompts import render_engineering_prompt
from molecule_ranker.codex_engineering.schemas import CodexEngineeringTask, EngineeringTaskType
from molecule_ranker.utils import slugify

DEFAULT_FORBIDDEN_ENGINEERING_COMMANDS = [
    "rm -rf",
    "git clean",
    "git reset --hard",
    "git checkout --",
    "sudo",
    "chmod -R 777",
    "chmod -r 777",
    "printenv",
    "cat .env",
    "gh auth token",
    "openai api key",
    "openai_api_key",
    "curl |",
    "| sh",
    "| bash",
]


def build_engineering_task(
    *,
    task_type: EngineeringTaskType,
    goal: str,
    working_directory: Path = Path("."),
    input_paths: list[Path] | None = None,
    log_text: str | None = None,
    apply: bool = False,
    allow_git_push: bool = False,
    allow_deletions: bool = False,
) -> CodexTask:
    engineering_task = CodexEngineeringTask(
        task_id=_task_id(task_type, goal),
        task_type=task_type,
        goal=goal,
        working_directory=working_directory.resolve(),
        input_paths=input_paths or [],
        log_text=log_text,
        apply=apply,
        allow_git_push=allow_git_push,
        allow_deletions=allow_deletions,
    )
    return to_codex_task(engineering_task)


def build_test_loop_task(
    test_output_path: Path,
    *,
    working_directory: Path = Path("."),
    apply: bool = False,
    allow_git_push: bool = False,
    allow_deletions: bool = False,
) -> CodexTask:
    log_text = test_output_path.read_text(errors="replace")
    return build_engineering_task(
        task_type="test_failure_analysis",
        goal=f"Analyze failing test output from {test_output_path}.",
        working_directory=working_directory,
        input_paths=[test_output_path],
        log_text=log_text,
        apply=apply,
        allow_git_push=allow_git_push,
        allow_deletions=allow_deletions,
    )


def build_docs_plan_task(
    section: Path,
    *,
    working_directory: Path = Path("."),
    apply: bool = False,
    allow_git_push: bool = False,
    allow_deletions: bool = False,
) -> CodexTask:
    log_text = (
        section.read_text(errors="replace")
        if section.exists() and section.is_file()
        else None
    )
    return build_engineering_task(
        task_type="docs_update_proposal",
        goal=f"Plan documentation updates for {section}.",
        working_directory=working_directory,
        input_paths=[section] if section.exists() else [],
        log_text=log_text,
        apply=apply,
        allow_git_push=allow_git_push,
        allow_deletions=allow_deletions,
    )


def to_codex_task(task: CodexEngineeringTask) -> CodexTask:
    return CodexTask(
        task_id=task.task_id,
        task_type="engineering_test_loop"
        if task.task_type in {"test_failure_analysis", "benchmark_failure_analysis"}
        else "engineering_plan",
        prompt=render_engineering_prompt(task),
        working_directory=str(task.working_directory.resolve()),
        input_artifact_paths=[
            str(path.resolve())
            for path in task.input_paths
            if path.exists() and path.is_file() and not _is_secret_or_env_path(path)
        ],
        allowed_commands=_allowed_commands(task),
        forbidden_commands=_forbidden_commands(task),
        expected_output_format="json",
        timeout_seconds=300,
        require_json=True,
        metadata={
            "engineering_task_type": task.task_type,
            "apply_enabled": task.apply,
            "allow_git_push": task.allow_git_push,
            "allow_deletions": task.allow_deletions,
        },
    )


def validate_engineering_task(task: CodexTask) -> list[str]:
    warnings: list[str] = []
    prompt_lower = _engineering_goal_text(task.prompt).lower()
    for forbidden in task.forbidden_commands:
        if forbidden.lower() in prompt_lower:
            warnings.append(f"Forbidden engineering command referenced: {forbidden}")
    for command in task.allowed_commands:
        lowered = command.lower()
        for forbidden in task.forbidden_commands:
            if forbidden.lower() in lowered:
                warnings.append(f"Forbidden engineering command allowlisted: {forbidden}")
    for artifact in task.input_artifact_paths:
        if _is_secret_or_env_path(Path(artifact)):
            warnings.append(f"Secret-like engineering artifact is not allowed: {artifact}")
    return warnings


def _engineering_goal_text(prompt: str) -> str:
    try:
        payload = json.loads(prompt)
    except Exception:
        return prompt
    if not isinstance(payload, dict):
        return prompt
    values = [
        str(payload.get("goal", "")),
        str(payload.get("log_text", "")),
    ]
    return "\n".join(values)


def _allowed_commands(task: CodexEngineeringTask) -> list[str]:
    commands: list[str] = list(DEFAULT_ENGINEERING_ALLOWED_COMMANDS)
    if task.apply:
        commands.extend(["git diff", "git status"])
    if task.allow_git_push:
        commands.append("git push")
    return commands


def _forbidden_commands(task: CodexEngineeringTask) -> list[str]:
    forbidden = list(DEFAULT_FORBIDDEN_ENGINEERING_COMMANDS)
    if not task.allow_git_push:
        forbidden.append("git push")
    if not task.allow_deletions:
        forbidden.extend(["rm ", "rm -r", "rm -rf", "git rm"])
    return sorted(set(forbidden))


def _is_secret_or_env_path(path: Path) -> bool:
    lowered = str(path).lower()
    return any(
        marker in lowered
        for marker in [
            ".env",
            "secret",
            "secrets",
            "credential",
            "credentials",
            "token",
            "id_rsa",
            "id_ed25519",
            ".pem",
        ]
    )


def _task_id(task_type: str, goal: str) -> str:
    digest = hashlib.sha256(goal.encode()).hexdigest()[:8]
    return slugify(f"{task_type}-{digest}")
