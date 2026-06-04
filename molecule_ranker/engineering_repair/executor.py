from __future__ import annotations

import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.engineering_repair.planner import regression_commands_for_report
from molecule_ranker.engineering_repair.schemas import (
    EngineeringCommandResult,
    EngineeringFailureReport,
    EngineeringRepairExecutionReport,
    EngineeringRepairPlan,
)

FORBIDDEN_COMMAND_FRAGMENTS = (
    "git push",
    "git reset --hard",
    "git checkout --",
    "git clean",
    "rm -rf",
    "rm -r",
    "sudo",
    "chmod -R 777",
    "printenv",
    "cat .env",
    "gh auth token",
    "| sh",
    "| bash",
)
ALLOWED_COMMAND_ROOTS = {
    "pytest",
    "ruff",
    "pyright",
    "mypy",
    "python",
    "uv",
    "molecule-ranker",
}


class EngineeringRepairExecutor:
    def __init__(self, *, cwd: Path = Path(".")) -> None:
        self.cwd = cwd.resolve()

    def run_repair(
        self,
        plan: EngineeringRepairPlan | Mapping[str, Any],
        *,
        dry_run: bool = True,
        apply: bool = False,
    ) -> EngineeringRepairExecutionReport:
        parsed = (
            plan
            if isinstance(plan, EngineeringRepairPlan)
            else EngineeringRepairPlan.model_validate(plan)
        )
        started = datetime.now(UTC)
        command_results: list[EngineeringCommandResult] = []
        rejected_actions: list[str] = []
        warnings: list[str] = []

        for action in parsed.actions:
            if action.requires_apply and not apply:
                rejected_actions.append(action.action_id)
                warnings.append(f"Action requires --apply: {action.summary}")
                continue
            if action.command is None:
                continue
            rejection = validate_engineering_command(action.command)
            if rejection:
                command_results.append(
                    EngineeringCommandResult(
                        command=action.command,
                        status="rejected",
                        rejection_reason=rejection,
                    )
                )
                rejected_actions.append(action.action_id)
                continue
            if dry_run:
                command_results.append(
                    EngineeringCommandResult(command=action.command, status="dry_run")
                )
                continue
            command_results.append(_run_command(action.command, cwd=self.cwd))

        if rejected_actions:
            status = "rejected"
        elif dry_run:
            status = "dry_run"
        elif all(
            result.returncode == 0
            for result in command_results
            if result.returncode is not None
        ):
            status = "succeeded"
        else:
            status = "failed"
        return EngineeringRepairExecutionReport(
            repair_plan_id=parsed.repair_plan_id,
            status=status,
            dry_run=dry_run,
            applied=apply and not dry_run,
            command_results=command_results,
            rejected_actions=rejected_actions,
            regression_commands=parsed.regression_commands,
            warnings=warnings,
            started_at=started,
            completed_at=datetime.now(UTC),
            metadata={
                "codex_profile": parsed.codex_profile,
                "code_changes_require_apply": True,
                "git_push_allowed": False,
            },
        )


def generate_regression_check_plan(
    failure_report: EngineeringFailureReport | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    commands = regression_commands_for_report(failure_report)
    rejected = [
        {"command": command, "reason": validate_engineering_command(command)}
        for command in commands
        if validate_engineering_command(command)
    ]
    return {
        "status": "rejected" if rejected else "generated",
        "dry_run_by_default": True,
        "commands": commands,
        "rejected_commands": rejected,
    }


def validate_engineering_command(command: list[str]) -> str | None:
    if not command:
        return "empty command rejected"
    joined = " ".join(command)
    lowered = joined.lower()
    for fragment in FORBIDDEN_COMMAND_FRAGMENTS:
        if fragment.lower() in lowered:
            return f"forbidden engineering command rejected: {fragment}"
    root = command[0]
    if root not in ALLOWED_COMMAND_ROOTS:
        return f"command is not allowlisted for engineering repair: {root}"
    if root == "python" and len(command) >= 3 and command[1] == "-m":
        if command[2] not in {"pytest", "pyright", "mypy"}:
            return f"python module is not allowlisted for engineering repair: {command[2]}"
    if root == "uv" and not any(
        token in command for token in {"pytest", "ruff", "pyright", "mypy"}
    ):
        return "uv command must run an approved engineering check"
    return None


def _run_command(command: list[str], *, cwd: Path) -> EngineeringCommandResult:
    started = datetime.now(UTC)
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return EngineeringCommandResult(
        command=command,
        status="succeeded" if completed.returncode == 0 else "failed",
        returncode=completed.returncode,
        stdout_excerpt=completed.stdout[-4000:],
        stderr_excerpt=completed.stderr[-4000:],
        duration_seconds=(datetime.now(UTC) - started).total_seconds(),
    )
