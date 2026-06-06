from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.subagents.consensus import synthesize_critique_consensus
from molecule_ranker.subagents.schemas import (
    SubagentConsensus,
    SubagentCritique,
    SubagentCritiqueType,
    SubagentResult,
    TaskRiskLevel,
)

SCIENCE_CLAIM_RE = re.compile(
    r"\b(?:PMID:?\s*\d{4,9}|10\.\d{4,9}/[-._;()/:A-Z0-9]+|"
    r"IC50|EC50|Ki|Kd|active|binds|binding|safe|effective|validated|confirmed)\b",
    re.I,
)
CLINICAL_SAFETY_RE = re.compile(
    r"\b(?:clinically safe|safe in patients|unsafe in patients|approved treatment)\b",
    re.I,
)
ANTIBODY_OVERCLAIM_RE = re.compile(
    r"\b(?:generated\s+)?(?:antibod(?:y|ies)|biologic(?:s)?|nanobod(?:y|ies)|"
    r"protein\s+binder(?:s)?)\b.{0,80}\b(?:binds?|binding|neutraliz(?:e|es|ing|ation)|"
    r"treats?|cures?|safe|developable|manufacturable|expressible)\b|"
    r"\b(?:binds?|binding|neutraliz(?:e|es|ing|ation)|treats?|cures?|safe|"
    r"developable|manufacturable|expressible)\b.{0,80}\b(?:generated\s+)?"
    r"(?:antibod(?:y|ies)|biologic(?:s)?|nanobod(?:y|ies)|protein\s+binder(?:s)?)\b",
    re.I,
)
BIOLOGICS_PROTOCOL_RE = re.compile(
    r"\b(?:expression\s+protocol|purification\s+protocol|expression/purification|"
    r"transfect|harvest\s+cells|protein\s+a\s+purification|elution\s+buffer|"
    r"immunization\s+protocol)\b",
    re.I,
)
POLICY_PERMISSION_RE = re.compile(
    r"\b(?:approve stage gate|approve campaign|external write|policy override|"
    r"destructive action)\b",
    re.I,
)
CONTRADICTION_RE = re.compile(r"\b(?:contradiction|contradicts|stale|outdated)\b", re.I)
HIGH_RISK_LEVELS = {"high", "critical"}


class CritiqueWorkflowError(ValueError):
    """Raised when a revision attempt violates critique workflow policy."""


class CritiqueAndReviseRecord(BaseModel):
    parent_session_id: str
    result_versions: list[SubagentResult]
    critiques: list[SubagentCritique]
    consensus: SubagentConsensus
    metadata: dict[str, Any] = Field(default_factory=dict)


class CritiqueAndReviseWorkflow:
    def run(
        self,
        *,
        parent_session_id: str,
        result: SubagentResult,
        critic_subagent_id: str = "guardrail-sentinel",
        expected_output_schema: dict[str, Any] | None = None,
        known_citations: set[str] | None = None,
        known_artifact_ids: set[str] | None = None,
        risk_level: TaskRiskLevel = "low",
        revision_output_json: dict[str, Any] | None = None,
        revision_output_text: str | None = None,
    ) -> CritiqueAndReviseRecord:
        versions = [result]
        critiques = review_result(
            result,
            critic_subagent_id=critic_subagent_id,
            expected_output_schema=expected_output_schema,
            known_citations=known_citations,
            known_artifact_ids=known_artifact_ids,
        )
        failed = [critique for critique in critiques if not critique.passed]
        if failed and _all_fixable(failed) and not _has_non_overridable_guardrail(failed):
            revised = revise_result(
                result,
                failed,
                expected_output_schema=expected_output_schema,
                known_artifact_ids=known_artifact_ids,
                revision_output_json=revision_output_json,
                revision_output_text=revision_output_text,
            )
            versions.append(revised)
            critiques.extend(
                review_result(
                    revised,
                    critic_subagent_id=critic_subagent_id,
                    expected_output_schema=expected_output_schema,
                    known_citations=known_citations,
                    known_artifact_ids=known_artifact_ids,
                )
            )

        consensus = synthesize_critique_consensus(
            parent_session_id=parent_session_id,
            task_ids=list(dict.fromkeys(result.task_id for result in versions)),
            results=versions,
            critiques=_critiques_for_latest_version(critiques, versions[-1].result_id),
            high_risk=risk_level in HIGH_RISK_LEVELS,
        )
        return CritiqueAndReviseRecord(
            parent_session_id=parent_session_id,
            result_versions=versions,
            critiques=critiques,
            consensus=consensus,
            metadata={"risk_level": risk_level},
        )


def review_result(
    result: SubagentResult,
    *,
    critic_subagent_id: str = "guardrail-sentinel",
    expected_output_schema: dict[str, Any] | None = None,
    known_citations: set[str] | None = None,
    known_artifact_ids: set[str] | None = None,
) -> list[SubagentCritique]:
    critiques: list[SubagentCritique] = []
    critiques.extend(_schema_critiques(result, critic_subagent_id, expected_output_schema))
    critiques.extend(_evidence_critiques(result, critic_subagent_id, known_citations))
    critiques.extend(_artifact_provenance_critiques(result, critic_subagent_id, known_artifact_ids))
    critiques.extend(_guardrail_critiques(result, critic_subagent_id))
    critiques.extend(_policy_permission_critiques(result, critic_subagent_id))
    critiques.extend(_uncertainty_critiques(result, critic_subagent_id))
    critiques.extend(_contradiction_critiques(result, critic_subagent_id))
    critiques.extend(_safety_critiques(result, critic_subagent_id))
    critiques.extend(_biologics_critiques(result, critic_subagent_id))
    critiques.extend(_operational_critiques(result, critic_subagent_id))
    if not critiques:
        critiques.append(
            _critique(
                critic_subagent_id=critic_subagent_id,
                result=result,
                critique_type="scientific_guardrail",
                passed=True,
                findings=["No blocking critique findings."],
                required_fixes=[],
                confidence=0.86,
                metadata={"fixable": False},
            )
        )
    return critiques


def revise_result(
    result: SubagentResult,
    critiques: list[SubagentCritique],
    *,
    expected_output_schema: dict[str, Any] | None = None,
    known_artifact_ids: set[str] | None = None,
    revision_output_json: dict[str, Any] | None = None,
    revision_output_text: str | None = None,
) -> SubagentResult:
    if _has_non_overridable_guardrail(critiques):
        raise CritiqueWorkflowError("guardrail failures cannot be revised or overridden")

    output_json = copy.deepcopy(result.output_json or {})
    if revision_output_json:
        _reject_unsupported_revision_facts(
            revision_output_json,
            artifact_ids=result.artifact_ids,
            known_artifact_ids=known_artifact_ids,
        )
        output_json.update(copy.deepcopy(revision_output_json))
    if expected_output_schema is not None:
        _apply_schema_defaults(output_json, expected_output_schema)

    output_text = revision_output_text if revision_output_text is not None else result.output_text
    if revision_output_text is not None:
        _reject_unsupported_revision_facts(
            revision_output_text,
            artifact_ids=result.artifact_ids,
            known_artifact_ids=known_artifact_ids,
        )

    previous_versions = [
        *[str(item) for item in result.metadata.get("previous_result_ids", [])],
        result.result_id,
    ]
    return SubagentResult(
        result_id=f"subagent-result-{uuid4().hex[:12]}",
        task_id=result.task_id,
        subagent_id=result.subagent_id,
        status="succeeded",
        output_json=output_json,
        output_text=output_text,
        artifact_ids=result.artifact_ids,
        tool_usage_ids=result.tool_usage_ids,
        confidence=min(result.confidence, 0.82),
        warnings=[
            warning
            for warning in result.warnings
            if "schema" not in warning.lower() and "missing" not in warning.lower()
        ],
        guardrail_findings=result.guardrail_findings,
        created_at=_now(),
        metadata={
            **result.metadata,
            "revision_of_result_id": result.result_id,
            "previous_result_ids": previous_versions,
            "revision_reason_critique_ids": [critique.critique_id for critique in critiques],
        },
    )


def _schema_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
    expected_output_schema: dict[str, Any] | None,
) -> list[SubagentCritique]:
    if expected_output_schema is None:
        return []
    errors = _validate_json_object(result.output_json or {}, expected_output_schema)
    if not errors:
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="output_schema_validity",
            passed=False,
            findings=[f"Output schema invalid: {error}" for error in errors],
            required_fixes=["Revise output_json to satisfy expected output schema."],
            confidence=0.94,
            metadata={"fixable": True, "issue_codes": ["schema_invalid"]},
        )
    ]


def _evidence_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
    known_citations: set[str] | None,
) -> list[SubagentCritique]:
    findings: list[str] = []
    for index, claim in enumerate(_claims(result.output_json)):
        citation = claim.get("citation") or claim.get("source_citation")
        if not citation:
            findings.append(f"Claim {index + 1} is missing a citation.")
        elif known_citations is not None and str(citation) not in known_citations:
            findings.append(f"Claim {index + 1} references unknown citation {citation}.")
    if SCIENCE_CLAIM_RE.search(_result_text(result)) and not result.artifact_ids:
        findings.append("Scientific claim is missing artifact grounding.")
    if not findings:
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="evidence_grounding",
            passed=False,
            findings=findings,
            required_fixes=["Attach existing citation or source artifact; do not invent one."],
            confidence=0.91,
            metadata={"fixable": True, "issue_codes": ["missing_citation"]},
        )
    ]


def _artifact_provenance_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
    known_artifact_ids: set[str] | None,
) -> list[SubagentCritique]:
    unknown = [
        artifact_id
        for artifact_id in result.artifact_ids
        if known_artifact_ids is not None and artifact_id not in known_artifact_ids
    ]
    missing_provenance = bool(result.artifact_ids) and not result.metadata.get(
        "artifact_provenance"
    )
    findings = [f"Unknown artifact reference {artifact_id}." for artifact_id in unknown]
    if missing_provenance:
        findings.append("Result references artifacts without artifact_provenance metadata.")
    if not findings:
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="artifact_provenance",
            passed=False,
            findings=findings,
            required_fixes=["Preserve artifact references and attach provenance metadata."],
            confidence=0.88,
            metadata={"fixable": True, "issue_codes": ["artifact_provenance_missing"]},
        )
    ]


def _guardrail_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    if result.status != "guardrail_failed" and not result.guardrail_findings:
        return []
    findings = ["Guardrail failure cannot be overridden by another subagent."]
    findings.extend(str(finding) for finding in result.guardrail_findings)
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="scientific_guardrail",
            passed=False,
            findings=findings,
            required_fixes=["Escalate to human review; do not override guardrail failure."],
            confidence=0.98,
            metadata={"fixable": False, "non_overridable": True},
        )
    ]


def _policy_permission_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    if not POLICY_PERMISSION_RE.search(_result_text(result)):
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="policy_permission",
            passed=False,
            findings=["Output attempts high-risk approval or policy action."],
            required_fixes=["Remove approval language and escalate for human review."],
            confidence=0.9,
            metadata={"fixable": True, "requires_human_review": True},
        )
    ]


def _uncertainty_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    if result.confidence >= 0.35:
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="uncertainty",
            passed=False,
            findings=["Result confidence is below acceptable critique threshold."],
            required_fixes=["Add uncertainty language or request human review."],
            confidence=0.8,
            metadata={"fixable": True},
        )
    ]


def _contradiction_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    if not CONTRADICTION_RE.search(" ".join([_result_text(result), *result.warnings])):
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="contradiction_staleness",
            passed=False,
            findings=["Result contains contradiction or staleness warning."],
            required_fixes=["Resolve contradiction or escalate residual disagreement."],
            confidence=0.84,
            metadata={"fixable": True},
        )
    ]


def _safety_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    if not CLINICAL_SAFETY_RE.search(_result_text(result)):
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="safety_developability",
            passed=False,
            findings=["Output makes a clinical safety/developability conclusion."],
            required_fixes=["Reframe as non-clinical triage or escalate."],
            confidence=0.89,
            metadata={"fixable": True},
        )
    ]


def _biologics_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    text = _result_text(result)
    findings: list[str] = []
    if ANTIBODY_OVERCLAIM_RE.search(text):
        findings.append("Output makes an unsupported generated antibody or biologics claim.")
    if BIOLOGICS_PROTOCOL_RE.search(text):
        findings.append("Output includes expression, purification, or immunization procedure text.")
    if not findings:
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="scientific_guardrail",
            passed=False,
            findings=findings,
            required_fixes=[
                "Remove unsupported biologics claims or procedure text and escalate if needed."
            ],
            confidence=0.93,
            metadata={
                "fixable": False,
                "non_overridable": True,
                "issue_codes": ["biologics_guardrail"],
            },
        )
    ]


def _operational_critiques(
    result: SubagentResult,
    critic_subagent_id: str,
) -> list[SubagentCritique]:
    if result.status not in {"failed", "partial", "validation_failed"}:
        return []
    return [
        _critique(
            critic_subagent_id=critic_subagent_id,
            result=result,
            critique_type="operational_reliability",
            passed=False,
            findings=[f"Result status {result.status} requires reliability review."],
            required_fixes=["Retry, revise, or escalate failed/partial result."],
            confidence=0.87,
            metadata={"fixable": result.status != "failed"},
        )
    ]


def _critique(
    *,
    critic_subagent_id: str,
    result: SubagentResult,
    critique_type: SubagentCritiqueType,
    passed: bool,
    findings: list[str],
    required_fixes: list[str],
    confidence: float,
    metadata: dict[str, Any],
) -> SubagentCritique:
    return SubagentCritique(
        critique_id=f"subagent-critique-{uuid4().hex[:12]}",
        critic_subagent_id=critic_subagent_id,
        target_result_id=result.result_id,
        critique_type=critique_type,
        passed=passed,
        findings=findings,
        required_fixes=required_fixes,
        confidence=confidence,
        metadata=metadata,
    )


def _claims(output_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(output_json, dict):
        return []
    claims = output_json.get("claims", [])
    if not isinstance(claims, list):
        return []
    return [claim for claim in claims if isinstance(claim, dict)]


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
    return errors


def _apply_schema_defaults(value: dict[str, Any], schema: dict[str, Any]) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        return
    for key in required:
        if not isinstance(key, str) or key in value:
            continue
        property_schema = properties.get(key, {})
        value[key] = _default_for_schema(
            property_schema if isinstance(property_schema, dict) else {}
        )


def _default_for_schema(schema: dict[str, Any]) -> Any:
    expected_type = schema.get("type")
    if expected_type == "string":
        return ""
    if expected_type == "array":
        return []
    if expected_type == "object":
        return {}
    if expected_type in {"number", "integer"}:
        return 0
    if expected_type == "boolean":
        return False
    return None


def _json_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def _reject_unsupported_revision_facts(
    value: Any,
    *,
    artifact_ids: list[str],
    known_artifact_ids: set[str] | None,
) -> None:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    if not SCIENCE_CLAIM_RE.search(text):
        return
    if not artifact_ids:
        raise CritiqueWorkflowError("revision cannot add unsupported facts")
    if known_artifact_ids is not None and not set(artifact_ids).issubset(known_artifact_ids):
        raise CritiqueWorkflowError("revision cannot reference unauthorized artifact facts")


def _all_fixable(critiques: list[SubagentCritique]) -> bool:
    return all(critique.metadata.get("fixable") is True for critique in critiques)


def _has_non_overridable_guardrail(critiques: list[SubagentCritique]) -> bool:
    return any(critique.metadata.get("non_overridable") is True for critique in critiques)


def _critiques_for_latest_version(
    critiques: list[SubagentCritique],
    latest_result_id: str,
) -> list[SubagentCritique]:
    latest = [
        critique for critique in critiques if critique.target_result_id == latest_result_id
    ]
    return latest or critiques


def _result_text(result: SubagentResult) -> str:
    return " ".join(
        [
            result.output_text,
            json.dumps(result.output_json or {}, sort_keys=True, default=str),
        ]
    )


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CritiqueAndReviseRecord",
    "CritiqueAndReviseWorkflow",
    "CritiqueWorkflowError",
    "review_result",
    "revise_result",
]
