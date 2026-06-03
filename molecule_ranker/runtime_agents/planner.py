from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from molecule_ranker.runtime_agents.schemas import RiskLevel, RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

DEFAULT_FORBIDDEN_ACTIONS = [
    "invent biomedical evidence",
    "invent assay results",
    "invent citations",
    "invent molecules outside generation pipeline",
    "change scores directly",
    "approve stage gates",
    "approve campaign advancement",
    "bypass deterministic validators",
    "bypass RBAC or policy",
    "provide medical advice",
    "provide protocols, synthesis instructions, or dosing",
]
UNSAFE_OUTPUT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:safe|active|effective|binding|synthesizable)\b", re.I), "overclaim"),
    (
        re.compile(r"\b(?:synthesis route|retrosynthesis|lab protocol|dosing|mg/kg)\b", re.I),
        "procedural guidance",
    ),
    (re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d", re.I), "invented assay result"),
    (
        re.compile(r"\bPMID:?\s*\d{4,9}\b|\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I),
        "invented citation",
    ),
)
FALLBACK_TEMPLATES: tuple[tuple[re.Pattern[str], str, list[str]], ...] = (
    (
        re.compile(r"\brank\b.*\bdisease\b|\bdisease\b.*\brank\b", re.I),
        "run_ranking",
        ["run:create"],
    ),
    (
        re.compile(r"\bgenerate\b.*\bcandidates?\b", re.I),
        "run_generation",
        ["generation:run"],
    ),
    (
        re.compile(r"\bcreate\b.*\breview workspace\b", re.I),
        "create_review_workspace",
        ["review:write"],
    ),
    (
        re.compile(r"\bimport\b.*\bassay results?\b", re.I),
        "import_assay_results",
        ["experiment:write"],
    ),
    (re.compile(r"\bbuild\b.*\bgraph\b", re.I), "build_graph", ["graph:build"]),
    (
        re.compile(r"\brun\b.*\bevaluation\b|\bevaluation\b", re.I),
        "run_benchmark",
        ["evaluation:run"],
    ),
)


class CodexPlannerUnavailable(RuntimeError):
    """Raised when Codex planning cannot be used and fallback should be attempted."""


class RuntimePlanValidationError(ValueError):
    """Raised when a Codex planner output fails deterministic validation."""


class CodexPlannerClient(Protocol):
    def plan(self, *, prompt: str, sandbox_mode: str, jsonl_output_path: str | None) -> str: ...


class SubprocessCodexPlannerClient:
    """Run Codex CLI in non-interactive exec mode for runtime planning."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        cwd: Path | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.command = command or ["codex", "exec", "--json"]
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds

    def plan(self, *, prompt: str, sandbox_mode: str, jsonl_output_path: str | None) -> str:
        command = list(self.command)
        if "--sandbox" not in command:
            command.extend(["--sandbox", sandbox_mode])
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise CodexPlannerUnavailable(str(exc)) from exc
        if jsonl_output_path:
            Path(jsonl_output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(jsonl_output_path).write_text(completed.stdout, encoding="utf-8")
        if completed.returncode != 0:
            raise CodexPlannerUnavailable(completed.stderr or "Codex planner failed.")
        return completed.stdout


class CodexRuntimePlanner:
    """Convert research goals into validated RuntimeActionPlan objects."""

    def __init__(
        self,
        *,
        registry: RuntimeToolRegistry | None = None,
        codex_client: CodexPlannerClient | None = None,
        jsonl_output_path: str | None = None,
        controlled_worker_mode: bool = False,
    ) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.codex_client = codex_client or SubprocessCodexPlannerClient()
        self.jsonl_output_path = jsonl_output_path
        self.controlled_worker_mode = controlled_worker_mode

    def plan(
        self,
        *,
        user_goal: str,
        session_id: str,
        project_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        project_context: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        current_artifacts: list[dict[str, Any]] | None = None,
        policy_constraints: list[str] | None = None,
        user_permissions: set[str] | list[str] | None = None,
        autonomy_level: str = "suggest_only",
    ) -> RuntimeActionPlan:
        allowed_tool_names = allowed_tools or self.registry.tool_names()
        context = _PlannerContext(
            user_goal=user_goal,
            session_id=session_id,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
            project_context=project_context or {},
            allowed_tools=allowed_tool_names,
            current_artifacts=current_artifacts or [],
            policy_constraints=policy_constraints or [],
            user_permissions=set(user_permissions or []),
            autonomy_level=autonomy_level,
        )
        prompt = self._build_prompt(context)
        try:
            output = self.codex_client.plan(
                prompt=prompt,
                sandbox_mode="workspace-write" if self.controlled_worker_mode else "read-only",
                jsonl_output_path=self.jsonl_output_path,
            )
            payload = _parse_planner_json(output)
            plan = RuntimeActionPlan.model_validate(payload)
        except CodexPlannerUnavailable:
            plan = self._fallback_plan(context)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RuntimePlanValidationError(f"Codex planner returned invalid JSON: {exc}") from exc
        return self._validate_plan(plan, context)

    def _build_prompt(self, context: _PlannerContext) -> str:
        allowed_specs = [self.registry.require(name) for name in context.allowed_tools]
        schema = RuntimeActionPlan.model_json_schema()
        payload = {
            "task": "Create a RuntimeActionPlan JSON object.",
            "user_goal": context.user_goal,
            "scope": {
                "session_id": context.session_id,
                "project_id": context.project_id,
                "org_id": context.org_id,
                "user_id": context.user_id,
                "autonomy_level": context.autonomy_level,
            },
            "project_context": context.project_context,
            "current_artifacts": context.current_artifacts,
            "policy_constraints": context.policy_constraints,
            "user_permissions": sorted(context.user_permissions),
            "allowed_tools": [
                {
                    "tool_name": spec.tool_name,
                    "category": spec.category,
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                    "output_schema": spec.output_schema,
                    "required_permissions": spec.required_permissions,
                    "policy_tags": spec.policy_tags,
                    "side_effect_level": spec.side_effect_level,
                    "requires_approval_by_default": spec.requires_approval_by_default,
                }
                for spec in allowed_specs
            ],
            "forbidden_actions": DEFAULT_FORBIDDEN_ACTIONS,
            "required_scientific_guardrails": [
                "Use allowed tools only.",
                "Do not invent evidence, results, citations, molecules, or scores.",
                "No medical advice.",
                "No protocols, synthesis instructions, or dosing.",
                "Preserve generated molecule hypothesis-only constraints.",
                "Codex tools cannot approve stage gates or campaign advancement.",
            ],
            "approval_rules": [
                "External writes require explicit approval.",
                "High-risk and high-cost jobs require approval.",
                "Generated molecule export requires approval.",
                "Campaign advancement and stage gates cannot be approved by Codex.",
            ],
            "output_json_schema": schema,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def _fallback_plan(self, context: _PlannerContext) -> RuntimeActionPlan:
        for pattern, tool_name, permissions in FALLBACK_TEMPLATES:
            if not pattern.search(context.user_goal):
                continue
            if tool_name not in context.allowed_tools:
                break
            step = RuntimeActionStep(
                step_id=f"step-{uuid4().hex[:12]}",
                plan_id=f"plan-{uuid4().hex[:12]}",
                step_index=0,
                action_type=tool_name,
                tool_name=tool_name,
                tool_args={"goal": context.user_goal},
                requires_approval=False,
                approval_reason=None,
                expected_outputs=[],
                status="pending",
                result_id=None,
                warnings=[],
                metadata={"fallback_template": True},
            )
            return RuntimeActionPlan(
                plan_id=step.plan_id,
                session_id=context.session_id,
                user_goal=context.user_goal,
                plan_summary=f"Deterministic fallback plan for {tool_name}.",
                steps=[step],
                required_approvals=[],
                expected_artifacts=[],
                risk_level="low",
                guardrail_warnings=[],
                created_by="deterministic_template",
                validated=False,
                validation_errors=[],
                metadata={"fallback_template": tool_name, "required_permissions": permissions},
            )
        raise CodexPlannerUnavailable("No deterministic fallback template matched the goal.")

    def _validate_plan(
        self,
        plan: RuntimeActionPlan,
        context: _PlannerContext,
    ) -> RuntimeActionPlan:
        validation_errors: list[str] = []
        guardrail_warnings = list(plan.guardrail_warnings)
        artifact_ids = {
            str(artifact.get("artifact_id"))
            for artifact in context.current_artifacts
            if artifact.get("artifact_id")
        }
        required_approvals = list(plan.required_approvals)
        risk_level = plan.risk_level
        tool_specs: dict[str, dict[str, Any]] = {}

        for step in plan.steps:
            if step.tool_name not in context.allowed_tools:
                validation_errors.append(f"Tool is not allowed: {step.tool_name}")
                continue
            spec = self.registry.get(step.tool_name)
            if spec is None:
                validation_errors.append(f"Tool does not exist in registry: {step.tool_name}")
                continue
            missing_permissions = [
                permission
                for permission in spec.required_permissions
                if permission not in context.user_permissions
            ]
            if missing_permissions:
                validation_errors.append(
                    f"Missing permission for {step.tool_name}: {', '.join(missing_permissions)}"
                )
            schema_errors = _validate_json_object(step.tool_args, spec.input_schema)
            validation_errors.extend(
                f"{step.tool_name} tool_args: {error}" for error in schema_errors
            )
            unsupported_refs = _unsupported_artifact_refs(step.tool_args, artifact_ids)
            validation_errors.extend(
                f"Unsupported artifact reference for {step.tool_name}: {artifact_id}"
                for artifact_id in unsupported_refs
            )
            if spec.requires_approval_by_default and not step.requires_approval:
                step.requires_approval = True
                step.approval_reason = "Tool requires approval by default."
            if spec.side_effect_level == "external_write":
                _append_unique(required_approvals, "external_write")
                risk_level = _max_risk(risk_level, "high")
            if step.requires_approval and not step.approval_reason:
                step.approval_reason = "Runtime policy requires approval."
            if spec.category == "codex" and (
                "stage_gate" in spec.policy_tags or "campaign_advance" in spec.policy_tags
            ):
                validation_errors.append("Codex tools cannot approve stage gates or campaigns.")
            tool_specs[spec.tool_name] = {
                "required_permissions": spec.required_permissions,
                "side_effect_level": spec.side_effect_level,
                "policy_tags": spec.policy_tags,
            }

        unsafe_warnings = _unsafe_output_warnings(plan)
        if unsafe_warnings:
            validation_errors.extend(
                f"Unsafe planner output: {warning}" for warning in unsafe_warnings
            )
            guardrail_warnings.extend(unsafe_warnings)
        if _has_forbidden_sequence(plan.steps):
            validation_errors.append("Forbidden tool sequence: Codex cannot advance campaigns.")
        if validation_errors:
            raise RuntimePlanValidationError("; ".join(validation_errors))
        plan.required_approvals = required_approvals
        plan.risk_level = risk_level
        plan.guardrail_warnings = guardrail_warnings
        plan.validated = True
        plan.validation_errors = []
        plan.metadata = {
            **plan.metadata,
            "tool_specs": tool_specs,
            "planner": "codex_runtime_planner",
            "sandbox_mode": "workspace-write" if self.controlled_worker_mode else "read-only",
        }
        return RuntimeActionPlan.model_validate(plan.model_dump(mode="python"))


class _PlannerContext:
    def __init__(
        self,
        *,
        user_goal: str,
        session_id: str,
        project_id: str | None,
        org_id: str | None,
        user_id: str | None,
        project_context: dict[str, Any],
        allowed_tools: list[str],
        current_artifacts: list[dict[str, Any]],
        policy_constraints: list[str],
        user_permissions: set[str],
        autonomy_level: str,
    ) -> None:
        self.user_goal = user_goal
        self.session_id = session_id
        self.project_id = project_id
        self.org_id = org_id
        self.user_id = user_id
        self.project_context = project_context
        self.allowed_tools = allowed_tools
        self.current_artifacts = current_artifacts
        self.policy_constraints = policy_constraints
        self.user_permissions = user_permissions
        self.autonomy_level = autonomy_level


def _parse_planner_json(output: str) -> dict[str, Any]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        for line in reversed(output.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            break
        else:
            raise
    if isinstance(payload, dict) and isinstance(payload.get("output"), dict):
        payload = payload["output"]
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("RuntimeActionPlan payload must be an object", output, 0)
    return payload


def _validate_json_object(value: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") != "object":
        return errors
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"missing required field {key}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, property_schema in properties.items():
            if key in value and isinstance(property_schema, dict):
                expected_type = property_schema.get("type")
                if expected_type and not _json_type_matches(value[key], str(expected_type)):
                    errors.append(f"{key} must be {expected_type}")
    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        extra = sorted(set(value) - set(properties))
        if extra:
            errors.append(f"unexpected fields: {', '.join(extra)}")
    return errors


def _json_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _unsupported_artifact_refs(
    tool_args: dict[str, Any], known_artifact_ids: set[str]
) -> list[str]:
    refs: list[str] = []
    for key, value in tool_args.items():
        if not key.endswith("artifact_id") and not key.endswith("artifact_ids"):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item not in known_artifact_ids:
                refs.append(item)
    return refs


def _unsafe_output_warnings(plan: RuntimeActionPlan) -> list[str]:
    text = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
    return [label for pattern, label in UNSAFE_OUTPUT_PATTERNS if pattern.search(text)]


def _has_forbidden_sequence(steps: list[RuntimeActionStep]) -> bool:
    names = [step.tool_name for step in steps]
    return any(
        name.startswith("summarize_") or name.startswith("draft_") for name in names
    ) and any("advance" in name or "stage_gate" in name for name in names)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _max_risk(current: RiskLevel, candidate: RiskLevel) -> RiskLevel:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return candidate if order[candidate] > order[current] else current


__all__ = [
    "CodexPlannerClient",
    "CodexPlannerUnavailable",
    "CodexRuntimePlanner",
    "RuntimePlanValidationError",
    "SubprocessCodexPlannerClient",
]
