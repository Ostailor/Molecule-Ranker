from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.agent_repair.schemas import AgentSelfEvaluation
from molecule_ranker.runtime_agents.approvals import approval_type_for_tool
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

FAKE_CITATION_RE = re.compile(
    r"\bPMID:?\s*\d{4,9}\b|\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b",
    re.I,
)
ASSAY_RESULT_RE = re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d", re.I)
MEDICAL_RE = re.compile(
    r"\b(?:diagnose|treat|treatment|patient|clinical use|prescribe)\b",
    re.I,
)
LAB_RE = re.compile(
    r"\b(?:lab protocol|incubate|centrifuge|pipette|plate cells|western blot|PCR)\b",
    re.I,
)
SYNTHESIS_RE = re.compile(
    r"\b(?:synthesis route|retrosynthesis|reaction scheme|reagent|solvent|yield)\b",
    re.I,
)
DOSING_RE = re.compile(r"\b(?:dose|dosing|mg/kg|mg per kg|patients? at)\b", re.I)
OVERCLAIM_RE = re.compile(
    r"\b(?:is|are|was|were|proved|proven|confirmed)\b.{0,50}"
    r"\b(?:safe|active|effective|binding|binds|synthesizable)\b",
    re.I,
)
GENERATED_ADVANCEMENT_KEYS = {
    "advance_generated_molecule",
    "advance_to_assay",
    "promote_generated_molecule",
}
RISKY_SIDE_EFFECTS = {"db_write", "external_write", "destructive", "codex_subprocess"}


class PlanEvaluator:
    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()

    def evaluate(
        self,
        plan: Any,
        *,
        approvals: set[str] | None = None,
        known_artifacts: set[str] | None = None,
        user_permissions: set[str] | None = None,
    ) -> AgentSelfEvaluation:
        payload = _payload(plan)
        plan_id = str(payload.get("plan_id") or payload.get("repair_plan_id") or "plan")
        session_id = _string_or_none(payload.get("session_id"))
        approved = approvals or set(payload.get("required_approvals") or [])
        known = known_artifacts or set(payload.get("known_artifacts") or [])
        permissions = user_permissions or set()
        findings: list[str] = []
        warnings: list[str] = []
        repairs: list[str] = []

        steps = _steps(payload)
        if not steps:
            findings.append("Plan has no executable steps.")
            repairs.append("Add at least one validated runtime action step.")

        artifact_writing_tools = 0
        for step in steps:
            step_id = str(step.get("step_id") or step.get("repair_action_id") or "step")
            tool_name = _string_or_none(step.get("tool_name"))
            if not tool_name:
                findings.append(f"{step_id}: missing tool_name.")
                repairs.append("Bind each step to an approved registered tool.")
                continue
            spec = self.registry.get(tool_name)
            if spec is None:
                findings.append(f"{step_id}: unknown tool `{tool_name}`.")
                repairs.append("Use only tools registered in RuntimeToolRegistry.")
                continue
            if not spec.required_permissions:
                findings.append(f"{tool_name}: tool spec declares no required permissions.")
                repairs.append("Add required permissions to the tool spec.")
            missing_permissions = [
                permission
                for permission in spec.required_permissions
                if permissions and permission not in permissions
            ]
            if missing_permissions:
                findings.append(
                    f"{tool_name}: missing permissions {', '.join(missing_permissions)}."
                )
                repairs.append("Request the required RBAC permission or choose a permitted tool.")
            findings.extend(
                f"{tool_name}: {error}"
                for error in _validate_tool_args(step.get("tool_args", {}), spec.input_schema)
            )
            approval_type = approval_type_for_tool(spec)
            requires_approval = bool(step.get("requires_approval")) or bool(
                spec.requires_approval_by_default
            )
            if spec.side_effect_level in RISKY_SIDE_EFFECTS:
                requires_approval = True
            if requires_approval and not _step_approved(step, approved, approval_type):
                findings.append(f"{tool_name}: required approval is missing.")
                repairs.append("Request human approval before executing the risky step.")
            if spec.side_effect_level == "external_write" and not _step_approved(
                step,
                approved,
                "external_write",
            ):
                findings.append(f"{tool_name}: external write without approval.")
                repairs.append(
                    "Add external_write approval or convert to a dry-run/read-only step."
                )
            if _generated_molecule_advancement_without_review(step):
                findings.append(
                    f"{tool_name}: generated molecule advancement without review is blocked."
                )
                repairs.append("Route generated molecules through review before advancement.")
            unknown_refs = sorted(_artifact_refs(step.get("tool_args", {})).difference(known))
            for artifact_id in unknown_refs:
                findings.append(f"{tool_name}: references unknown artifact `{artifact_id}`.")
                repairs.append("Provide the referenced artifact or remove the reference.")
            if spec.side_effect_level == "artifact_write":
                artifact_writing_tools += 1

        expected_artifacts = _string_list(payload.get("expected_artifacts"))
        if artifact_writing_tools and not expected_artifacts:
            warnings.append("Plan writes artifacts but declares no expected_artifacts.")
            repairs.append("Declare expected artifacts for artifact-writing steps.")
        if _has_unsafe_sequence(steps):
            findings.append(
                "Unsafe action sequence: generation cannot flow directly to assay/campaign."
            )
            repairs.append("Insert expert review or validation before assay/campaign advancement.")

        return _evaluation(
            session_id=session_id,
            evaluated_object_type="runtime_plan",
            evaluated_object_id=plan_id,
            evaluation_type="pre_execution",
            findings=findings,
            required_repairs=repairs,
            warnings=warnings,
            metadata={"step_count": len(steps)},
        )


class OutputEvaluator:
    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()

    def evaluate(
        self,
        output: Any,
        *,
        tool_name: str | None = None,
        known_artifacts: set[str] | None = None,
        known_entities: set[str] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> AgentSelfEvaluation:
        payload = _payload(output)
        result_id = str(payload.get("result_id") or payload.get("output_id") or "output")
        schema = output_schema
        if schema is None and tool_name:
            spec = self.registry.get(tool_name)
            schema = spec.output_schema if spec is not None else None
        output_body = payload.get("output", payload)
        findings: list[str] = []
        warnings: list[str] = []
        repairs: list[str] = []

        if schema is not None:
            schema_errors = _validate_json_object(output_body, schema)
            findings.extend(f"Output schema invalid: {error}" for error in schema_errors)
            if schema_errors:
                repairs.append("Regenerate or transform output to satisfy the output schema.")

        known_artifact_ids = known_artifacts or set(_string_list(payload.get("artifact_ids")))
        known_entity_ids = known_entities or set()
        unknown_artifacts = sorted(_artifact_refs(output_body).difference(known_artifact_ids))
        unknown_entities = sorted(_entity_refs(output_body).difference(known_entity_ids))
        for artifact_id in unknown_artifacts:
            findings.append(f"Output references unknown artifact `{artifact_id}`.")
            repairs.append("Ground output in known artifacts only.")
        for entity_id in unknown_entities:
            findings.append(f"Output references unknown entity `{entity_id}`.")
            repairs.append("Resolve or remove unknown entity references.")

        text = _text(output_body)
        _append_forbidden_output_findings(text, findings, repairs)
        if "limitations" not in _lower_keys(output_body) and "limitation" not in text.lower():
            findings.append("Output does not include limitations.")
            repairs.append("Add explicit limitations to the output.")
        if not _is_artifact_grounded(payload):
            findings.append("Output is not artifact-grounded.")
            repairs.append("Attach grounded_artifact_ids, referenced_artifact_ids, or provenance.")

        return _evaluation(
            session_id=_string_or_none(payload.get("session_id")),
            evaluated_object_type="tool_result",
            evaluated_object_id=result_id,
            evaluation_type="post_execution",
            findings=findings,
            required_repairs=repairs,
            warnings=warnings,
            metadata={"tool_name": tool_name},
        )


class ArtifactEvaluator:
    def evaluate(
        self,
        artifact: Any,
        *,
        required_source_record_ids: bool = False,
    ) -> AgentSelfEvaluation:
        payload = _payload(artifact) if artifact is not None else {}
        artifact_id = str(payload.get("artifact_id") or payload.get("id") or "artifact")
        findings: list[str] = []
        repairs: list[str] = []

        if not artifact:
            findings.append("Artifact does not exist.")
            repairs.append("Create or restore the missing artifact.")
        if not _has_schema_contract(payload):
            findings.append("Artifact schema contract is missing or invalid.")
            repairs.append("Revalidate artifact against a declared schema contract.")
        if not payload.get("provenance"):
            findings.append("Artifact provenance is missing.")
            repairs.append("Attach source-backed provenance before use.")
        if required_source_record_ids and not _string_list(payload.get("source_record_ids")):
            findings.append("Artifact requires source_record_ids.")
            repairs.append("Attach source_record_ids from upstream source records.")
        if _is_generated_molecule_artifact(payload) and not _generated_molecules_labeled(payload):
            findings.append("Generated molecules are not labeled as generated hypotheses.")
            repairs.append("Label generated molecules and keep them out of evidence.")
        if _prediction_promoted_to_evidence(payload):
            findings.append("Model prediction artifact is promoted to evidence.")
            repairs.append("Keep predictions distinct from evidence artifacts.")
        if _codex_output_promoted_to_evidence(payload):
            findings.append("Codex output is promoted to evidence.")
            repairs.append("Store Codex output as assistant context, not evidence.")

        return _evaluation(
            session_id=_string_or_none(payload.get("session_id")),
            evaluated_object_type="artifact",
            evaluated_object_id=artifact_id,
            evaluation_type="artifact_completeness",
            findings=findings,
            required_repairs=repairs,
            warnings=[],
            metadata={"artifact_type": payload.get("artifact_type") or payload.get("kind")},
        )


class WorkflowEvaluator:
    def evaluate(self, workflow: Any) -> AgentSelfEvaluation:
        payload = _payload(workflow)
        workflow_id = str(payload.get("workflow_id") or payload.get("session_id") or "workflow")
        findings: list[str] = []
        repairs: list[str] = []

        created_artifacts = set(_string_list(payload.get("artifacts_created")))
        created_artifacts.update(_string_list(payload.get("artifact_ids")))
        for artifact_id in _string_list(payload.get("required_artifacts")):
            if artifact_id not in created_artifacts:
                findings.append(f"Required artifact `{artifact_id}` was not created.")
                repairs.append("Rerun or repair the step that should produce the missing artifact.")
        optional_failures = payload.get("optional_failures", [])
        if isinstance(optional_failures, list):
            for failure in optional_failures:
                if isinstance(failure, Mapping) and not failure.get("handled"):
                    findings.append("Optional failure was not handled.")
                    repairs.append("Record a skip, retry, or user-visible limitation.")
        if payload.get("guardrails_passed") is False:
            findings.append("Workflow guardrails did not pass.")
            repairs.append("Stop workflow and resolve guardrail findings.")
        if not payload.get("audit_entries") and not payload.get("audit_events"):
            findings.append("Workflow audit entries are missing.")
            repairs.append("Write audit events for workflow execution and repair decisions.")
        if not payload.get("expected_next_step"):
            findings.append("Expected next step is unavailable.")
            repairs.append("Create a safe next-step recommendation or human escalation.")

        return _evaluation(
            session_id=_string_or_none(payload.get("session_id")),
            evaluated_object_type="workflow",
            evaluated_object_id=workflow_id,
            evaluation_type="operational",
            findings=findings,
            required_repairs=repairs,
            warnings=[],
            metadata={},
        )


class ScientificIntegrityEvaluator:
    def evaluate(self, obj: Any, *, object_id: str = "scientific-object") -> AgentSelfEvaluation:
        payload = _payload(obj)
        text = _text(payload)
        findings: list[str] = []
        repairs: list[str] = []

        if _fake_evidence(payload) or FAKE_CITATION_RE.search(text):
            findings.append("Output contains fake or ungrounded evidence/citation.")
            repairs.append("Remove invented evidence and cite only known source records.")
        if _fake_assay_result(payload) or ASSAY_RESULT_RE.search(text):
            findings.append("Output contains fake or ungrounded assay results.")
            repairs.append("Use only imported, validated assay result artifacts.")
        if _generated_direct_evidence(payload):
            findings.append(
                "Generated molecule is promoted to direct evidence without exact result."
            )
            repairs.append("Keep generated molecules as hypotheses until exact results exist.")
        if _model_prediction_evidence(payload):
            findings.append("Model prediction is promoted to evidence.")
            repairs.append("Keep model predictions as prioritization artifacts.")
        if _docking_binding_evidence(payload) or "docking score proves binding" in text.lower():
            findings.append("Docking score is promoted to binding evidence.")
            repairs.append("Treat docking as a heuristic, not binding evidence.")
        if _graph_inference_evidence(payload):
            findings.append("Graph inference is promoted to evidence.")
            repairs.append("Keep graph inference as hypothesis context.")
        if OVERCLAIM_RE.search(text):
            findings.append("Generated overclaim detected.")
            repairs.append("Remove unsupported safety/activity/binding claims.")

        return _evaluation(
            session_id=_string_or_none(payload.get("session_id")),
            evaluated_object_type="codex_output",
            evaluated_object_id=object_id,
            evaluation_type="scientific_integrity",
            findings=findings,
            required_repairs=repairs,
            warnings=[],
            metadata={},
        )


def _evaluation(
    *,
    session_id: str | None,
    evaluated_object_type: str,
    evaluated_object_id: str,
    evaluation_type: str,
    findings: list[str],
    required_repairs: list[str],
    warnings: list[str],
    metadata: dict[str, Any],
) -> AgentSelfEvaluation:
    finding_count = len(findings)
    warning_count = len(warnings)
    denominator = max(1, finding_count + warning_count + 1)
    score = 1.0 - (finding_count + (warning_count * 0.25)) / denominator
    return AgentSelfEvaluation(
        evaluation_id=f"agent-self-eval-{uuid4().hex[:12]}",
        session_id=session_id,
        subagent_id=None,
        task_id=None,
        evaluated_object_type=evaluated_object_type,  # type: ignore[arg-type]
        evaluated_object_id=evaluated_object_id,
        evaluation_type=evaluation_type,  # type: ignore[arg-type]
        passed=not findings,
        score=max(0.0, min(1.0, score)),
        findings=list(dict.fromkeys(findings)),
        required_repairs=list(dict.fromkeys(required_repairs)),
        warnings=list(dict.fromkeys(warnings)),
        created_at=datetime.now(UTC),
        metadata=metadata,
    )


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        return {"text": value}
    return {}


def _steps(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_steps = plan.get("steps", plan.get("actions", []))
    if not isinstance(raw_steps, list):
        return []
    return [_payload(step) for step in raw_steps]


def _validate_tool_args(args: Any, schema: Mapping[str, Any]) -> list[str]:
    if not isinstance(args, dict):
        return ["tool_args must be an object."]
    return _validate_json_object(args, schema)


def _validate_json_object(value: Any, schema: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") == "object" and not isinstance(value, dict):
        return ["value must be an object."]
    if not isinstance(value, dict):
        return errors
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"missing required field `{key}`")
    properties = schema.get("properties", {})
    if isinstance(properties, Mapping):
        for key, property_schema in properties.items():
            if key in value and isinstance(property_schema, Mapping):
                expected_type = property_schema.get("type")
                if isinstance(expected_type, str) and not _matches_json_type(
                    value[key],
                    expected_type,
                ):
                    errors.append(f"`{key}` must be {expected_type}")
    return errors


def _matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _step_approved(
    step: Mapping[str, Any],
    approvals: set[str],
    approval_type: str | None,
) -> bool:
    step_approvals = set(_string_list(step.get("approvals")))
    approval_reason = _string_or_none(step.get("approval_reason"))
    approval_tokens = approvals | step_approvals
    return bool(
        approval_reason
        or step.get("approved")
        or step.get("approval_id")
        or (approval_type and approval_type in approval_tokens)
        or step.get("tool_name") in approval_tokens
    )


def _generated_molecule_advancement_without_review(step: Mapping[str, Any]) -> bool:
    args = step.get("tool_args", {})
    if not isinstance(args, Mapping):
        return False
    normalized_keys = {
        re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_") for key in args
    }
    if GENERATED_ADVANCEMENT_KEYS.intersection(normalized_keys):
        return not bool(args.get("reviewed") or args.get("review_artifact_id"))
    target_stage = str(args.get("target_stage") or args.get("advance_to") or "").lower()
    if target_stage in {"assay", "campaign", "stage_gate"}:
        return not bool(args.get("reviewed") or args.get("review_artifact_id"))
    return False


def _has_unsafe_sequence(steps: list[dict[str, Any]]) -> bool:
    categories = [
        str(step.get("tool_name") or step.get("action_type") or "").lower()
        for step in steps
    ]
    text = " ".join(categories)
    has_generation = "generation" in text or "generated" in text
    advances = "assay" in text or "campaign" in text or "stage_gate" in text
    reviewed = "review" in text or "validation" in text
    return has_generation and advances and not reviewed


def _artifact_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"artifact_id", "source_artifact_id", "review_artifact_id"}:
                refs.update(_string_list(item))
            elif key_text in {
                "artifact_ids",
                "source_artifact_ids",
                "referenced_artifact_ids",
                "grounded_artifact_ids",
            }:
                refs.update(_string_list(item))
            refs.update(_artifact_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_artifact_refs(item))
    return refs


def _entity_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"entity_id", "target_id", "disease_id", "molecule_id"}:
                refs.update(_string_list(item))
            elif key_text in {"entity_ids", "target_ids", "disease_ids", "molecule_ids"}:
                refs.update(_string_list(item))
            refs.update(_entity_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_entity_refs(item))
    return refs


def _append_forbidden_output_findings(
    text: str,
    findings: list[str],
    repairs: list[str],
) -> None:
    if FAKE_CITATION_RE.search(text):
        findings.append("Output appears to fabricate citations.")
        repairs.append(
            "Remove invented citations and ground citations in known literature artifacts."
        )
    if ASSAY_RESULT_RE.search(text):
        findings.append("Output appears to fabricate assay results.")
        repairs.append("Remove invented assay values and reference validated assay artifacts.")
    if OVERCLAIM_RE.search(text):
        findings.append("Output contains unsupported safety/activity/binding claims.")
        repairs.append("Rewrite output as limitations-backed operational context.")
    for label, pattern in {
        "medical advice": MEDICAL_RE,
        "lab protocol": LAB_RE,
        "synthesis instructions": SYNTHESIS_RE,
        "dosing guidance": DOSING_RE,
    }.items():
        if pattern.search(text):
            findings.append(f"Output contains forbidden {label}.")
            repairs.append(f"Remove forbidden {label} from the output.")


def _is_artifact_grounded(payload: Mapping[str, Any]) -> bool:
    if _string_list(payload.get("artifact_ids")):
        provenance = payload.get("metadata", {}).get("artifact_provenance")
        return isinstance(provenance, Mapping) or bool(
            _string_list(payload.get("grounded_artifact_ids"))
        )
    body = payload.get("output", payload)
    return bool(_artifact_refs(body))


def _has_schema_contract(payload: Mapping[str, Any]) -> bool:
    return bool(
        payload.get("schema_version")
        or payload.get("artifact_contract_version")
        or payload.get("contract_version")
    )


def _is_generated_molecule_artifact(payload: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(payload.get(key, ""))
        for key in ("artifact_type", "kind", "type", "category")
    ).lower()
    return "generated" in text and "molecule" in text


def _generated_molecules_labeled(payload: Mapping[str, Any]) -> bool:
    molecules = payload.get("molecules", payload.get("generated_molecules", []))
    if not isinstance(molecules, list):
        return bool(payload.get("generated") or payload.get("hypothesis_label"))
    if not molecules:
        return bool(payload.get("generated") or payload.get("hypothesis_label"))
    for molecule in molecules:
        item = _payload(molecule)
        label_text = " ".join(
            str(item.get(key, "")) for key in ("label", "status", "kind", "artifact_type")
        ).lower()
        if not (item.get("generated") or "generated" in label_text or "hypothesis" in label_text):
            return False
    return True


def _prediction_promoted_to_evidence(payload: Mapping[str, Any]) -> bool:
    artifact_type = str(payload.get("artifact_type") or payload.get("kind") or "").lower()
    evidence_type = str(payload.get("evidence_type") or payload.get("record_type") or "").lower()
    return "prediction" in artifact_type and "evidence" in evidence_type


def _codex_output_promoted_to_evidence(payload: Mapping[str, Any]) -> bool:
    source = str(payload.get("source") or payload.get("created_by") or "").lower()
    artifact_type = str(payload.get("artifact_type") or payload.get("kind") or "").lower()
    return "codex" in source and "evidence" in artifact_type


def _fake_evidence(payload: Mapping[str, Any]) -> bool:
    if payload.get("evidence_item") or payload.get("evidence_items"):
        return not bool(payload.get("source_record_ids") or payload.get("source_artifact_ids"))
    return False


def _fake_assay_result(payload: Mapping[str, Any]) -> bool:
    if payload.get("assay_result") or payload.get("assay_results"):
        return not bool(payload.get("source_record_ids") or payload.get("assay_artifact_id"))
    return False


def _generated_direct_evidence(payload: Mapping[str, Any]) -> bool:
    text = _text(payload).lower()
    generated = "generated" in text and "molecule" in text
    direct_evidence = (
        "direct evidence" in text
        or str(payload.get("evidence_type", "")).lower() == "direct"
    )
    has_exact_result = bool(payload.get("exact_result_id") or payload.get("assay_result_id"))
    return generated and direct_evidence and not has_exact_result


def _model_prediction_evidence(payload: Mapping[str, Any]) -> bool:
    text = _text(payload).lower()
    return ("model prediction" in text or payload.get("prediction") is not None) and (
        "evidence" in text or str(payload.get("record_type", "")).lower() == "evidence"
    )


def _docking_binding_evidence(payload: Mapping[str, Any]) -> bool:
    text = _text(payload).lower()
    return ("docking score" in text or payload.get("docking_score") is not None) and (
        "binding evidence" in text or "proves binding" in text
    )


def _graph_inference_evidence(payload: Mapping[str, Any]) -> bool:
    text = _text(payload).lower()
    return ("graph inference" in text or payload.get("graph_inference") is not None) and (
        "evidence" in text or str(payload.get("record_type", "")).lower() == "evidence"
    )


def _lower_keys(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    return {str(key).lower() for key in value}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        parts: list[str] = []
        for item in value.values():
            parts.append(_text(item))
        return " ".join(part for part in parts if part)
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    return "" if value is None else str(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | set | tuple):
        return [item for item in value if isinstance(item, str)]
    return []


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "ArtifactEvaluator",
    "OutputEvaluator",
    "PlanEvaluator",
    "ScientificIntegrityEvaluator",
    "WorkflowEvaluator",
]
