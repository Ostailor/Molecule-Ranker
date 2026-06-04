from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.codex_engineering import build_engineering_task, build_test_loop_task
from molecule_ranker.codex_engineering.task_builder import DEFAULT_FORBIDDEN_ENGINEERING_COMMANDS
from molecule_ranker.engineering_repair.schemas import (
    ENGINEERING_CODEX_PROFILE,
    EngineeringFailure,
    EngineeringFailureReport,
    EngineeringRepairAction,
    EngineeringRepairPlan,
)

SECRET_REDACTION_MARKERS = ("[REDACTED",)
PYTEST_FAILED_PATTERN = re.compile(r"^FAILED\s+([^\s:]+)(?:::([^\s]+))?\s+-\s+(.+)$")
PYTEST_ERROR_PATTERN = re.compile(r"^ERROR\s+([^\s:]+)(?:::([^\s]+))?\s+-\s+(.+)$")
FILE_LINE_PATTERN = re.compile(r"([A-Za-z0-9_./-]+\.py):(\d+):")


def diagnose_engineering_failures(
    test_output_path: Path,
    *,
    max_excerpt_chars: int = 12000,
) -> EngineeringFailureReport:
    raw = test_output_path.read_text(encoding="utf-8", errors="replace")
    redacted = redact_secrets(raw)
    failures = _parse_failures(redacted)
    if not failures and redacted.strip():
        failures = [
            EngineeringFailure(
                category=_category_from_text(redacted),
                summary=_first_nonempty_line(redacted) or "Engineering check failed.",
                excerpt=redacted[:1000],
            )
        ]
    redaction_warnings = (
        ["Secret-like values were redacted from engineering repair input."]
        if any(marker in redacted for marker in SECRET_REDACTION_MARKERS)
        else []
    )
    task = build_test_loop_task(test_output_path, working_directory=Path("."))
    return EngineeringFailureReport(
        source_path=str(test_output_path),
        summary=_report_summary(failures),
        failures=failures,
        redaction_warnings=redaction_warnings,
        metadata={
            "raw_excerpt": redacted[:max_excerpt_chars],
            "codex_task_type": task.task_type,
            "codex_profile": ENGINEERING_CODEX_PROFILE,
            "codex_prompt": task.prompt,
        },
    )


def plan_engineering_repair(
    failure_report: EngineeringFailureReport | Mapping[str, Any],
) -> EngineeringRepairPlan:
    report = (
        failure_report
        if isinstance(failure_report, EngineeringFailureReport)
        else EngineeringFailureReport.model_validate(failure_report)
    )
    regression_commands = regression_commands_for_report(report)
    actions = [
        EngineeringRepairAction(
            action_type="inspect_failure",
            summary="Inspect failing tests, lint/type errors, and the smallest related code paths.",
            target_files=_target_files(report),
            metadata={"failure_report_id": report.failure_report_id},
        )
    ]
    for command in regression_commands:
        actions.append(
            EngineeringRepairAction(
                action_type=_action_type_for_command(command),
                summary=f"Run regression command: {' '.join(command)}",
                command=command,
                side_effect_level="none",
                metadata={"generated_from": "engineering_failure_report"},
            )
        )
    actions.append(
        EngineeringRepairAction(
            action_type="propose_patch",
            summary=(
                "Ask Codex engineering profile to propose a scoped patch plan. "
                "Do not edit code unless --apply is supplied to run-repair."
            ),
            target_files=_target_files(report),
            side_effect_level="none",
            requires_apply=False,
            metadata={
                "codex_task": build_engineering_task(
                    task_type="bug_fix_planning",
                    goal=(
                        "Diagnose engineering failures and propose a safe repair plan "
                        f"for {report.failure_report_id}."
                    ),
                    working_directory=Path("."),
                    log_text=report.metadata.get("raw_excerpt", ""),
                    apply=False,
                    allow_git_push=False,
                    allow_deletions=False,
                ).model_dump(mode="json"),
            },
        )
    )
    return EngineeringRepairPlan(
        failure_report_id=report.failure_report_id,
        summary=f"Engineering repair plan for {len(report.failures)} reported failure(s).",
        actions=actions,
        regression_commands=regression_commands,
        forbidden_commands=sorted(
            set(DEFAULT_FORBIDDEN_ENGINEERING_COMMANDS + ["git push", "git reset --hard"])
        ),
        metadata={
            "codex_profile": ENGINEERING_CODEX_PROFILE,
            "source_path": report.source_path,
            "failure_categories": sorted({failure.category for failure in report.failures}),
        },
    )


def regression_commands_for_report(
    failure_report: EngineeringFailureReport | Mapping[str, Any] | None = None,
) -> list[list[str]]:
    if failure_report is None:
        return [
            ["ruff", "check", "."],
            ["python", "-m", "pyright"],
            ["pytest", "-q"],
        ]
    report = (
        failure_report
        if isinstance(failure_report, EngineeringFailureReport)
        else EngineeringFailureReport.model_validate(failure_report)
    )
    commands: list[list[str]] = []
    categories = {failure.category for failure in report.failures}
    files = _target_files(report)
    if "lint_failure" in categories:
        commands.append(["ruff", "check", *files] if files else ["ruff", "check", "."])
    if "typecheck_failure" in categories:
        commands.append(
            ["python", "-m", "pyright", *files] if files else ["python", "-m", "pyright"]
        )
    if "schema_contract_failure" in categories:
        commands.append(["pytest", "-q", "tests/test_v2_release_contract_freeze.py"])
    if "docs_check_failure" in categories:
        commands.append(["pytest", "-q", "tests/test_user_docs.py", "tests/test_admin_docs.py"])
    test_targets = _test_targets(report)
    if test_targets:
        commands.append(["pytest", "-q", *test_targets])
    if not commands:
        commands.append(["pytest", "-q"])
    return _dedupe_commands(commands)


def _parse_failures(text: str) -> list[EngineeringFailure]:
    failures: list[EngineeringFailure] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = PYTEST_FAILED_PATTERN.match(stripped) or PYTEST_ERROR_PATTERN.match(stripped)
        if match:
            file_path, test_name, summary = match.groups()
            failures.append(
                EngineeringFailure(
                    category=_category_from_text(stripped),
                    summary=summary[:500],
                    file_path=file_path,
                    test_name=test_name,
                    error_type=_error_type(summary),
                    excerpt=_window(lines, index),
                )
            )
            continue
        if "error:" in stripped.lower() and (": " in stripped or ".py:" in stripped):
            file_match = FILE_LINE_PATTERN.search(stripped)
            failures.append(
                EngineeringFailure(
                    category=_category_from_text(stripped),
                    summary=stripped[:500],
                    file_path=file_match.group(1) if file_match else None,
                    line=int(file_match.group(2)) if file_match else None,
                    error_type=_error_type(stripped),
                    excerpt=_window(lines, index),
                )
            )
    return failures


def _category_from_text(text: str) -> str:
    lowered = text.lower()
    if "ruff" in lowered or "lint" in lowered:
        return "lint_failure"
    if "pyright" in lowered or "mypy" in lowered or "type error" in lowered:
        return "typecheck_failure"
    if "schema" in lowered or "contract" in lowered:
        return "schema_contract_failure"
    if "docs" in lowered or "documentation" in lowered:
        return "docs_check_failure"
    if "failed" in lowered or "error" in lowered or "assertionerror" in lowered:
        return "test_failure"
    return "unknown"


def _target_files(report: EngineeringFailureReport) -> list[str]:
    return sorted(
        {
            failure.file_path
            for failure in report.failures
            if failure.file_path and failure.file_path.endswith(".py")
        }
    )


def _test_targets(report: EngineeringFailureReport) -> list[str]:
    targets: list[str] = []
    for failure in report.failures:
        if not failure.file_path or not failure.file_path.startswith("tests/"):
            continue
        targets.append(
            f"{failure.file_path}::{failure.test_name}"
            if failure.test_name
            else failure.file_path
        )
    return sorted(set(targets))


def _action_type_for_command(command: list[str]) -> str:
    joined = " ".join(command)
    if "ruff" in command:
        return "run_lint"
    if "pyright" in joined or "mypy" in joined:
        return "run_typecheck"
    if "docs" in joined:
        return "run_docs_check"
    if "schema" in joined or "contract" in joined:
        return "run_schema_check"
    return "run_regression_command"


def _report_summary(failures: list[EngineeringFailure]) -> str:
    if not failures:
        return "No failures were parsed from the supplied engineering output."
    categories = sorted({failure.category for failure in failures})
    return f"Parsed {len(failures)} engineering failure(s): {', '.join(categories)}."


def _error_type(summary: str) -> str | None:
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failure))\b", summary)
    return match.group(1) if match else None


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:500]
    return None


def _window(lines: list[str], index: int, *, radius: int = 2) -> str:
    start = max(0, index - radius)
    stop = min(len(lines), index + radius + 1)
    return "\n".join(lines[start:stop])[:2000]


def _dedupe_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return deduped
