from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.approvals import approval_type_for_tool
from molecule_ranker.runtime_agents.context import redact_sensitive_context
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

GuardrailScope = Literal["plan", "output", "state"]
GuardrailSeverity = Literal["warning", "block"]

FAKE_CITATION_RE = re.compile(
    r"\bPMID:?\s*(?P<pmid>\d{4,9})\b|\b(?P<doi>10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b",
    re.I,
)
ASSAY_RESULT_RE = re.compile(r"\b(?:IC50|EC50|Ki|Kd)\s*(?:=|:|of)\s*\d", re.I)
MOLECULE_RE = re.compile(r"\b(?:SMILES|InChI)\s*[:=]\s*[A-Za-z0-9@+\-\[\]\(\)=#$\\/%.]+", re.I)
SCORE_MUTATION_KEYS = {
    "new_score",
    "override_score",
    "score",
    "score_updates",
    "scores",
    "updated_score",
}
EVIDENCE_MUTATION_KEYS = {"evidenceitem", "evidence_item", "evidence_updates"}
CLAIM_RE = re.compile(
    r"\b(?:is|are|was|were|proved|proven|confirmed)\b.{0,40}"
    r"\b(?:safe|active|effective|binding|binds|synthesizable)\b",
    re.I,
)
MEDICAL_ADVICE_RE = re.compile(
    r"\b(?:diagnose|treat|treatment|patient|clinical use|prescribe|contraindication)\b",
    re.I,
)
LAB_PROTOCOL_RE = re.compile(
    r"\b(?:lab protocol|incubate|centrifuge|pipette|plate cells|western blot|PCR)\b",
    re.I,
)
SYNTHESIS_RE = re.compile(
    r"\b(?:synthesis route|retrosynthesis|reaction scheme|reagent|solvent|yield)\b",
    re.I,
)
DOSING_RE = re.compile(r"\b(?:dose|dosing|mg/kg|mg per kg|patients? at)\b", re.I)
SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|bearer|client[_-]?secret|password|secret|token)"
    r"\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)


class RuntimeGuardrailViolation(BaseModel):
    scope: GuardrailScope
    code: str
    message: str
    severity: GuardrailSeverity = "block"
    object_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeGuardrailResult(BaseModel):
    allowed: bool
    violations: list[RuntimeGuardrailViolation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RuntimeGuardrailChecker:
    """Deterministic guardrails for runtime-agent plans, outputs, and state."""

    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()

    def check_plan(
        self,
        plan: RuntimeActionPlan,
        *,
        user_permissions: set[str] | None = None,
        approvals: set[str] | None = None,
        actor: str = "codex",
        known_artifacts: set[str] | None = None,
    ) -> RuntimeGuardrailResult:
        violations: list[RuntimeGuardrailViolation] = []
        permissions = user_permissions or set()
        approved = approvals or set()
        artifact_ids = known_artifacts or set()
        runtime_context = plan.metadata.get("runtime_context")
        if not isinstance(runtime_context, dict):
            runtime_context = {}

        for step in plan.steps:
            spec = self.registry.get(step.tool_name)
            if spec is None:
                violations.append(
                    _violation("plan", "unknown_tool", f"Unknown tool: {step.tool_name}.")
                )
                continue
            missing_permissions = [
                permission
                for permission in spec.required_permissions
                if permissions and permission not in permissions
            ]
            if missing_permissions:
                violations.append(
                    _violation(
                        "plan",
                        "unauthorized_tool",
                        f"Unauthorized tool {step.tool_name}: {', '.join(missing_permissions)}.",
                        object_id=step.step_id,
                    )
                )
            approval_type = approval_type_for_tool(spec)
            if not self.registry.tool_allowed_in_context(
                spec,
                org_id=runtime_context.get("org_id")
                if isinstance(runtime_context.get("org_id"), str)
                else None,
                project_id=runtime_context.get("project_id")
                if isinstance(runtime_context.get("project_id"), str)
                else None,
                user_id=runtime_context.get("user_id")
                if isinstance(runtime_context.get("user_id"), str)
                else actor,
                user_permissions=permissions,
            ):
                violations.append(
                    _violation(
                        "plan",
                        "tool_not_approved_for_context",
                        f"Tool is not approved for this project/org context: {step.tool_name}.",
                        object_id=step.step_id,
                    )
                )
            if approval_type is not None and not _is_approved(step, approved, approval_type):
                violations.append(
                    _violation(
                        "plan",
                        "missing_approval",
                        f"{approval_type} approval is missing for {step.tool_name}.",
                        object_id=step.step_id,
                    )
                )
            if spec.side_effect_level == "external_write" and not _is_approved(
                step, approved, "external_write"
            ):
                violations.append(
                    _violation(
                        "plan",
                        "external_write_without_approval",
                        f"external write without approval: {step.tool_name}.",
                        object_id=step.step_id,
                    )
                )
            if ("stage_gate" in spec.policy_tags or "campaign_advance" in spec.policy_tags) and (
                actor == "codex"
            ):
                violations.append(
                    _violation(
                        "plan",
                        "stage_gate_approval_by_codex",
                        "Stage gate approval by Codex is blocked.",
                        object_id=step.step_id,
                    )
                )
            if _advances_generated_molecule_without_review(step.tool_args):
                violations.append(
                    _violation(
                        "plan",
                        "generated_molecule_advancement_without_review",
                        "generated molecule advancement without review is blocked.",
                        object_id=step.step_id,
                    )
                )
            unsupported_refs = _unknown_artifact_refs(step.tool_args, artifact_ids)
            for artifact_id in unsupported_refs:
                violations.append(
                    _violation(
                        "plan",
                        "unknown_artifact_reference",
                        f"Output references unknown artifact: {artifact_id}.",
                        object_id=step.step_id,
                    )
                )
            if _requires_artifact_provenance(spec) and not _has_artifact_provenance(step.tool_args):
                violations.append(
                    _violation(
                        "plan",
                        "no_artifact_provenance",
                        f"No artifact provenance supplied for {step.tool_name}.",
                        object_id=step.step_id,
                    )
                )

        if _has_unsafe_sequence(plan):
            violations.append(
                _violation(
                    "plan",
                    "unsafe_sequence",
                    "Unsafe sequence: generation cannot advance directly to campaign or assay.",
                )
            )
        return RuntimeGuardrailResult(allowed=not violations, violations=violations)

    def check_output(
        self,
        output: RuntimeToolResult | dict[str, Any] | str,
        *,
        known_citations: set[str] | None = None,
        known_molecules: set[str] | None = None,
        allow_raw_assay_results: bool = False,
    ) -> RuntimeGuardrailResult:
        violations: list[RuntimeGuardrailViolation] = []
        payload = _output_payload(output)
        text = _payload_text(payload)
        known_citations = known_citations or set()
        known_molecules = known_molecules or set()

        for citation in _citations(text):
            if citation not in known_citations:
                violations.append(
                    _violation("output", "fake_citation", f"Blocked fake citation: {citation}.")
                )
        if ASSAY_RESULT_RE.search(text) and not allow_raw_assay_results:
            violations.append(
                _violation("output", "fake_assay_result", "Blocked fake assay result.")
            )
        for molecule in _molecules(text):
            if molecule not in known_molecules:
                violations.append(
                    _violation("output", "fake_molecule", "Blocked fake molecule text.")
                )
        if _contains_score_mutation(payload):
            violations.append(_violation("output", "fake_score", "Blocked fake score mutation."))
        if CLAIM_RE.search(text):
            violations.append(
                _violation("output", "unsupported_claim", "Blocked unsupported activity claim.")
            )
        if MEDICAL_ADVICE_RE.search(text):
            violations.append(
                _violation("output", "clinical_medical_advice", "Blocked clinical/medical advice.")
            )
        if LAB_PROTOCOL_RE.search(text):
            violations.append(_violation("output", "lab_protocol", "Blocked lab protocol text."))
        if SYNTHESIS_RE.search(text):
            violations.append(
                _violation("output", "synthesis_instruction", "Blocked synthesis instruction.")
            )
        if DOSING_RE.search(text):
            violations.append(
                _violation("output", "dosing_patient_guidance", "Blocked dosing guidance.")
            )
        if SECRET_RE.search(text) or redact_sensitive_context(text) != text:
            violations.append(_violation("output", "secret_leakage", "Blocked secret leakage."))
        return RuntimeGuardrailResult(allowed=not violations, violations=violations)

    def check_state(
        self,
        result: RuntimeToolResult | dict[str, Any],
        *,
        expected_output_schema: dict[str, Any] | None = None,
        known_artifacts: set[str] | None = None,
        known_citations: set[str] | None = None,
        known_entities: set[str] | None = None,
    ) -> RuntimeGuardrailResult:
        violations: list[RuntimeGuardrailViolation] = []
        payload = _output_payload(result)
        output = payload.get("output") if isinstance(payload, dict) else payload
        output_dict = output if isinstance(output, dict) else {}
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        if expected_output_schema is not None:
            for error in _validate_json_object(output_dict, expected_output_schema):
                violations.append(
                    _violation(
                        "state",
                        "tool_result_schema_mismatch",
                        f"Tool result schema mismatch: {error}.",
                    )
                )
        artifact_ids = (
            set(_list(payload.get("artifact_ids"))) if isinstance(payload, dict) else set()
        )
        known_artifacts = known_artifacts or set()
        for artifact_id in artifact_ids:
            if known_artifacts and artifact_id not in known_artifacts:
                violations.append(
                    _violation(
                        "state",
                        "unknown_artifact_reference",
                        f"Output references unknown artifact: {artifact_id}.",
                        object_id=artifact_id,
                    )
                )
        provenance = metadata.get("artifact_provenance") if isinstance(metadata, dict) else None
        if artifact_ids and not provenance:
            violations.append(
                _violation(
                    "state",
                    "artifact_missing_provenance",
                    "Artifact contract mismatch: artifact missing provenance.",
                )
            )
        if _contains_score_mutation(output_dict):
            violations.append(
                _violation(
                    "state",
                    "unsupported_score_mutation",
                    "Codex output attempted unsupported score mutation.",
                )
            )
        if _contains_evidence_mutation(output_dict):
            violations.append(
                _violation(
                    "state",
                    "evidence_direct_mutation",
                    "Codex output attempts to mutate evidence directly.",
                )
            )
        text = _payload_text(payload)
        for citation in _citations(text):
            if known_citations is not None and citation not in known_citations:
                violations.append(
                    _violation(
                        "state",
                        "unknown_citation_reference",
                        f"Output references unknown citation: {citation}.",
                    )
                )
        for entity in _entity_refs(payload):
            if known_entities is not None and entity not in known_entities:
                violations.append(
                    _violation(
                        "state",
                        "unknown_entity_reference",
                        f"Output references unknown entity: {entity}.",
                    )
                )
        return RuntimeGuardrailResult(allowed=not violations, violations=violations)


def _violation(
    scope: GuardrailScope,
    code: str,
    message: str,
    *,
    object_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeGuardrailViolation:
    return RuntimeGuardrailViolation(
        scope=scope,
        code=code,
        message=message,
        object_id=object_id,
        metadata=metadata or {},
    )


def _is_approved(step: Any, approvals: set[str], approval_type: str) -> bool:
    return bool({approval_type, step.step_id, step.tool_name}.intersection(approvals))


def _advances_generated_molecule_without_review(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    text = _payload_text(value).lower()
    has_generated = "generated_molecule" in text or "generated molecule" in text
    advances = any(
        term in text
        for term in (
            "advance_to_assay",
            "campaign_advance",
            "stage_gate",
            "assay_advancement",
        )
    )
    reviewed = "reviewed" in text or "review_workspace" in text or "human_review" in text
    return has_generated and advances and not reviewed


def _unknown_artifact_refs(value: Any, known_artifacts: set[str]) -> list[str]:
    if not known_artifacts:
        return []
    refs: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).endswith("artifact_id") and isinstance(item, str):
                if item not in known_artifacts:
                    refs.append(item)
            refs.extend(_unknown_artifact_refs(item, known_artifacts))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_unknown_artifact_refs(item, known_artifacts))
    return refs


def _requires_artifact_provenance(spec: RuntimeToolSpec) -> bool:
    return spec.side_effect_level == "artifact_write" and spec.category in {
        "generation",
        "hypotheses",
        "portfolio",
    }


def _has_artifact_provenance(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("source_artifact_id", "provenance", "artifact_provenance"))


def _has_unsafe_sequence(plan: RuntimeActionPlan) -> bool:
    seen_generation = False
    for step in plan.steps:
        if step.tool_name in {"run_generation", "run_design_loop"}:
            seen_generation = True
        if seen_generation and step.tool_name in {
            "create_campaign",
            "plan_campaign",
            "replan_campaign",
            "import_assay_results",
            "link_assay_results",
        }:
            return True
    return False


def _output_payload(output: RuntimeToolResult | dict[str, Any] | str) -> dict[str, Any] | str:
    if isinstance(output, RuntimeToolResult):
        return output.model_dump(mode="json")
    return output


def _payload_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _citations(text: str) -> list[str]:
    citations: list[str] = []
    for match in FAKE_CITATION_RE.finditer(text):
        if match.group("pmid"):
            citations.append(f"PMID:{match.group('pmid')}")
        elif match.group("doi"):
            citations.append(match.group("doi"))
    return citations


def _molecules(text: str) -> list[str]:
    return [match.group(0) for match in MOLECULE_RE.finditer(text)]


def _contains_score_mutation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SCORE_MUTATION_KEYS:
                return True
            if _contains_score_mutation(item):
                return True
    elif isinstance(value, list):
        return any(_contains_score_mutation(item) for item in value)
    return False


def _contains_evidence_mutation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in EVIDENCE_MUTATION_KEYS:
                return True
            if _contains_evidence_mutation(item):
                return True
    elif isinstance(value, list):
        return any(_contains_evidence_mutation(item) for item in value)
    return False


def _entity_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).endswith("entity_id") and isinstance(item, str):
                refs.append(item)
            refs.extend(_entity_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_entity_refs(item))
    return refs


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _validate_json_object(value: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if schema.get("type") != "object":
        return errors
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"missing required output field {key}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, property_schema in properties.items():
            if key in value and isinstance(property_schema, dict):
                expected_type = property_schema.get("type")
                if expected_type and not _json_type_matches(value[key], str(expected_type)):
                    errors.append(f"output field {key} must be {expected_type}")
    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        extra = sorted(set(value) - set(properties))
        if extra:
            errors.append(f"unexpected output fields: {', '.join(extra)}")
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


__all__ = [
    "RuntimeGuardrailChecker",
    "RuntimeGuardrailResult",
    "RuntimeGuardrailViolation",
]
