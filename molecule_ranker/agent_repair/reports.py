from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molecule_ranker.agent_repair.schemas import (
    AgentSelfEvaluation,
    FailureDiagnosis,
    RegressionCheck,
    RepairExecution,
    RepairPlan,
)
from molecule_ranker.codex_backbone.guardrails import redact_secrets

REPAIR_ARTIFACT_FILENAMES = {
    "self_evaluation": "self_evaluation.json",
    "failure_diagnosis": "failure_diagnosis.json",
    "repair_plan": "repair_plan.json",
    "repair_execution": "repair_execution.json",
    "regression_checks": "regression_checks.json",
    "repair_report": "repair_report.md",
}
REPAIR_REPORT_SECTIONS = [
    "Failure Summary",
    "Diagnosis",
    "Root Cause Evidence",
    "Repair Plan",
    "Approval Requirements",
    "Repair Execution",
    "Regression Checks",
    "Remaining Risks",
    "Next Recommended Actions",
    "Limitations",
]
REPAIR_REPORT_DISCLAIMERS = [
    "Repairs are operational workflow repairs.",
    "Repairs do not fabricate scientific evidence.",
    "Repairs do not validate molecules.",
    "This report provides no medical/lab/synthesis/dosing guidance.",
]
PROHIBITED_INPUT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmedical advice\b", re.I), "[REDACTED_PROHIBITED_GUIDANCE]"),
    (re.compile(r"\blab protocols?\b", re.I), "[REDACTED_PROHIBITED_GUIDANCE]"),
    (re.compile(r"\bsynthesis routes?\b", re.I), "[REDACTED_PROHIBITED_GUIDANCE]"),
    (re.compile(r"\bretrosynthesis\b", re.I), "[REDACTED_PROHIBITED_GUIDANCE]"),
    (re.compile(r"\bdosing\b", re.I), "[REDACTED_PROHIBITED_GUIDANCE]"),
    (
        re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d+(?:\.\d+)?\s*\w*", re.I),
        "[REDACTED_UNVERIFIED_ASSAY_CLAIM]",
    ),
)


def write_repair_artifacts(
    output_dir: str | Path,
    *,
    self_evaluation: AgentSelfEvaluation | list[AgentSelfEvaluation] | None = None,
    failure_diagnosis: FailureDiagnosis | None = None,
    repair_plan: RepairPlan | None = None,
    repair_execution: RepairExecution | None = None,
    regression_checks: list[RegressionCheck] | None = None,
) -> dict[str, Path]:
    """Write the canonical V2.4 repair artifact bundle."""

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    checks = list(regression_checks or [])
    payloads: dict[str, Any] = {
        "self_evaluation": _jsonable(self_evaluation),
        "failure_diagnosis": _jsonable(failure_diagnosis),
        "repair_plan": _jsonable(repair_plan),
        "repair_execution": _jsonable(repair_execution),
        "regression_checks": [_jsonable(check) for check in checks],
    }
    paths: dict[str, Path] = {}
    for key, filename in REPAIR_ARTIFACT_FILENAMES.items():
        path = target / filename
        paths[key] = path
        if key == "repair_report":
            continue
        _write_json(path, payloads[key])
    report_path = target / REPAIR_ARTIFACT_FILENAMES["repair_report"]
    report_path.write_text(
        render_repair_report_markdown(
            self_evaluation=self_evaluation,
            failure_diagnosis=failure_diagnosis,
            repair_plan=repair_plan,
            repair_execution=repair_execution,
            regression_checks=checks,
        ),
        encoding="utf-8",
    )
    return paths


def render_repair_report_markdown(
    *,
    self_evaluation: AgentSelfEvaluation | list[AgentSelfEvaluation] | None = None,
    failure_diagnosis: FailureDiagnosis | None = None,
    repair_plan: RepairPlan | None = None,
    repair_execution: RepairExecution | None = None,
    regression_checks: list[RegressionCheck] | None = None,
) -> str:
    checks = list(regression_checks or [])
    lines = [
        "# Repair Report",
        "",
        *[f"- {disclaimer}" for disclaimer in REPAIR_REPORT_DISCLAIMERS],
        "",
        "## Failure Summary",
        _failure_summary(failure_diagnosis, repair_execution),
        "",
        "## Diagnosis",
        _diagnosis_summary(failure_diagnosis),
        "",
        "## Root Cause Evidence",
        *_root_cause_evidence(failure_diagnosis),
        "",
        "## Repair Plan",
        *_repair_plan_lines(repair_plan),
        "",
        "## Approval Requirements",
        *_approval_lines(repair_plan, repair_execution),
        "",
        "## Repair Execution",
        *_execution_lines(repair_execution),
        "",
        "## Regression Checks",
        *_regression_lines(checks),
        "",
        "## Remaining Risks",
        *_remaining_risks(failure_diagnosis, repair_execution, checks),
        "",
        "## Next Recommended Actions",
        *_next_actions(repair_plan, repair_execution, checks),
        "",
        "## Limitations",
        *_limitations(self_evaluation, failure_diagnosis),
        "",
    ]
    return "\n".join(lines)


def _failure_summary(
    diagnosis: FailureDiagnosis | None,
    execution: RepairExecution | None,
) -> str:
    if diagnosis is None:
        return "No failure diagnosis artifact was supplied."
    status = execution.status if execution is not None else "not_executed"
    return _clean_text(
        f"{diagnosis.failure_object_type} `{diagnosis.failure_object_id}` failed as "
        f"`{diagnosis.failure_category}`. Repair execution status: `{status}`."
    )


def _diagnosis_summary(diagnosis: FailureDiagnosis | None) -> str:
    if diagnosis is None:
        return "No diagnosis was recorded."
    return _clean_text(
        f"{diagnosis.root_cause_summary} Confidence: {diagnosis.confidence:.2f}. "
        f"Repairability: `{diagnosis.repairability}`."
    )


def _root_cause_evidence(diagnosis: FailureDiagnosis | None) -> list[str]:
    if diagnosis is None or not diagnosis.evidence:
        return ["- No root cause evidence was recorded."]
    return [
        f"- {_clean_text(json.dumps(item, sort_keys=True, default=str))}"
        for item in diagnosis.evidence
    ]


def _repair_plan_lines(plan: RepairPlan | None) -> list[str]:
    if plan is None:
        return ["- No repair plan was recorded."]
    lines = [
        f"- Plan: `{_clean_text(plan.repair_plan_id)}`",
        f"- Summary: {_clean_text(plan.plan_summary)}",
        f"- Validated: `{plan.validated}`",
    ]
    if plan.validation_errors:
        lines.append("- Validation errors: " + _clean_text("; ".join(plan.validation_errors)))
    for action in plan.actions:
        lines.append(
            "- Action "
            f"`{_clean_text(action.repair_action_id)}`: `{action.action_type}` on "
            f"`{_clean_text(action.target_object_type)}`/`{_clean_text(action.target_object_id)}`; "
            f"risk `{action.risk_level}`, side effect `{action.side_effect_level}`."
        )
    return lines


def _approval_lines(
    plan: RepairPlan | None,
    execution: RepairExecution | None,
) -> list[str]:
    lines: list[str] = []
    if plan is not None:
        lines.append(f"- Human approval required by plan: `{plan.requires_human_approval}`")
        for action in plan.actions:
            if action.requires_approval:
                reason = action.approval_reason or "Approval required."
                lines.append(
                    f"- `{_clean_text(action.repair_action_id)}` requires approval: "
                    f"{_clean_text(reason)}"
                )
    if execution is not None and execution.approvals_requested:
        lines.append(
            "- Approval requests: "
            + _clean_text(", ".join(execution.approvals_requested))
        )
    return lines or ["- No approval requirements were recorded."]


def _execution_lines(execution: RepairExecution | None) -> list[str]:
    if execution is None:
        return ["- Repair was not executed."]
    lines = [
        f"- Execution: `{_clean_text(execution.repair_execution_id)}`",
        f"- Status: `{execution.status}`",
        f"- Artifacts created: {_clean_text(', '.join(execution.artifacts_created) or 'none')}",
        f"- Artifacts modified: {_clean_text(', '.join(execution.artifacts_modified) or 'none')}",
        f"- Jobs created: {_clean_text(', '.join(execution.jobs_created) or 'none')}",
    ]
    for action in execution.executed_actions:
        lines.append(
            "- Executed action: "
            + _clean_text(json.dumps(action, sort_keys=True, default=str))
        )
    return lines


def _regression_lines(checks: list[RegressionCheck]) -> list[str]:
    if not checks:
        return ["- No regression checks were recorded."]
    return [
        "- "
        f"`{_clean_text(check.regression_check_id)}` `{check.check_type}` "
        f"passed=`{check.passed}` findings={_clean_text('; '.join(check.findings) or 'none')}"
        for check in checks
    ]


def _remaining_risks(
    diagnosis: FailureDiagnosis | None,
    execution: RepairExecution | None,
    checks: list[RegressionCheck],
) -> list[str]:
    risks: list[str] = []
    if diagnosis is not None:
        risks.extend(_clean_text(warning) for warning in diagnosis.warnings)
    if execution is not None:
        risks.extend(_clean_text(warning) for warning in execution.warnings)
    risks.extend(
        _clean_text(
            f"Regression check {check.regression_check_id} failed: "
            f"{'; '.join(check.findings)}"
        )
        for check in checks
        if not check.passed
    )
    return [f"- {risk}" for risk in risks] or ["- No remaining operational risks were recorded."]


def _next_actions(
    plan: RepairPlan | None,
    execution: RepairExecution | None,
    checks: list[RegressionCheck],
) -> list[str]:
    if execution is None:
        return ["- Review the repair plan and execute only allowed operational repairs."]
    if execution.status in {"approval_required", "guardrail_blocked"}:
        return ["- Resolve human approval or guardrail review before execution."]
    if checks and not all(check.passed for check in checks):
        return ["- Investigate failed regression checks before marking repair complete."]
    if execution.status == "succeeded":
        return ["- Archive the repair artifacts and continue normal workflow monitoring."]
    if plan is not None and plan.requires_human_approval:
        return ["- Confirm approval decisions and rerun regression checks."]
    return ["- Review warnings and decide whether another bounded repair attempt is appropriate."]


def _limitations(
    self_evaluation: AgentSelfEvaluation | list[AgentSelfEvaluation] | None,
    diagnosis: FailureDiagnosis | None,
) -> list[str]:
    limitations = [
        "- This report is an operational audit artifact, not a scientific evidence source.",
        "- It records workflow repair decisions and does not validate molecules or claims.",
        "- It contains no medical/lab/synthesis/dosing guidance.",
    ]
    evaluations = self_evaluation if isinstance(self_evaluation, list) else [self_evaluation]
    failed = [item for item in evaluations if item is not None and not item.passed]
    if failed:
        limitations.append("- One or more self-evaluations failed and require review.")
    if diagnosis is not None and diagnosis.confidence < 0.5:
        limitations.append("- Diagnosis confidence is low; human review is recommended.")
    return limitations


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _clean_text(value: Any) -> str:
    text = redact_secrets(str(value))
    for pattern, replacement in PROHIBITED_INPUT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


__all__ = [
    "REPAIR_ARTIFACT_FILENAMES",
    "REPAIR_REPORT_DISCLAIMERS",
    "REPAIR_REPORT_SECTIONS",
    "render_repair_report_markdown",
    "write_repair_artifacts",
]
