from __future__ import annotations

import re
from typing import Any

from molecule_ranker.experiments.guardrails import (
    sanitize_experimental_output_text,
    should_omit_experimental_output_key,
)
from molecule_ranker.review.schemas import ReviewWorkspace, ValidationHandoff

GENERATED_DIRECT_EVIDENCE_NOTICE = "Generated molecules have no direct experimental evidence."

ALLOWED_SUGGESTED_ASSAY_CLASSES = [
    "biochemical target engagement assay",
    "orthogonal binding assay",
    "cellular pathway modulation assay",
    "disease-relevant phenotypic assay",
    "counter-screen for related targets",
    "cytotoxicity triage assay",
    "metabolic stability triage",
    "permeability triage",
    "expert medicinal chemistry review",
    "expert toxicology review",
]

_FORBIDDEN_DETAIL_TERMS = (
    "administer",
    "concentration",
    "dose",
    "dosage",
    "incubat",
    "mg/kg",
    "protocol",
    "reagent",
    "reaction condition",
    "synthesis",
    "temperature",
)


def build_validation_handoff(
    workspace: ReviewWorkspace,
    review_item_id: str,
    *,
    evidence_packet_paths: dict[str, str] | None = None,
) -> ValidationHandoff:
    item = _get_item(workspace, review_item_id)
    paths = _artifact_paths(workspace, item)
    paths.update(evidence_packet_paths or {})
    questions = _validation_questions(item)
    categories = _suggested_validation_categories(item)
    roles = _required_expert_roles(item)
    key_risks = _risk_questions(item)
    return ValidationHandoff(
        review_item_id=review_item_id,
        candidate_name=item.candidate_name,
        candidate_origin=item.candidate_origin,
        disease_name=item.disease_name,
        target_symbols=item.target_symbols,
        validation_questions=questions,
        suggested_assay_classes=categories,
        required_expert_reviews=roles,
        key_risks_to_check=key_risks,
        evidence_packet_paths=paths,
        disclaimer=(
            "Research expert handoff only. This is not medical advice and does not "
            "provide operational methods, clinical-use instructions, or chemistry "
            "build details."
        ),
        metadata={
            "workspace_id": workspace.workspace_id,
            "candidate_identity": {
                "candidate_id": item.candidate_id,
                "candidate_name": item.candidate_name,
                "candidate_origin": item.candidate_origin,
                "canonical_smiles": item.canonical_smiles,
                "model_score": item.score,
                "model_confidence": item.confidence,
            },
            "key_hypothesis": _key_hypothesis(item),
            "evidence_summary": _evidence_summary(item),
            "experimental_result_summary": _experimental_result_summary(item),
            "key_uncertainties": _key_uncertainties(item),
            "risk_flags": list(item.risk_flags),
            "supporting_artifact_paths": paths,
            "suggested_validation_categories": categories,
            "content_boundary": (
                "High-level validation planning only; no operational lab details are included."
            ),
        },
    )


def _validation_questions(item: Any) -> list[str]:
    questions = [
        f"What evidence would most directly test the target rationale for {item.disease_name}?",
        f"What orthogonal evidence would clarify candidate interaction with {_target_text(item)}?",
        "What evidence would challenge the current ranking or priority bucket?",
    ]
    if item.candidate_origin == "generated":
        questions.insert(0, GENERATED_DIRECT_EVIDENCE_NOTICE)
        questions.append("What additional computational checks are needed before expert handoff?")
    if _has_safety_risk(item):
        questions.append("What safety warning evidence should an expert review?")
    if _has_developability_risk(item):
        questions.append(
            "What developability limitations should a medicinal chemistry expert review?"
        )
    questions.extend(_risk_questions(item))
    return _deduplicate(questions)


def _suggested_validation_categories(item: Any) -> list[str]:
    categories = [
        "biochemical target engagement assay",
        "orthogonal binding assay",
        "cellular pathway modulation assay",
        "disease-relevant phenotypic assay",
        "counter-screen for related targets",
    ]
    if _has_safety_risk(item):
        categories.extend(["cytotoxicity triage assay", "expert toxicology review"])
    if _has_developability_risk(item):
        categories.extend(
            [
                "metabolic stability triage",
                "permeability triage",
                "expert medicinal chemistry review",
            ]
        )
    if item.candidate_origin == "generated":
        categories.append("expert medicinal chemistry review")
    return [
        category
        for category in ALLOWED_SUGGESTED_ASSAY_CLASSES
        if category in set(categories)
    ]


def _required_expert_roles(item: Any) -> list[str]:
    roles = ["biologist", "pharmacologist"]
    if str(getattr(item, "item_type", "")) in {"biologic", "generated_antibody"}:
        roles.extend(["biologics scientist", "antibody engineer", "developability expert"])
    if item.candidate_origin == "generated" or _has_developability_risk(item):
        roles.append("medicinal_chemist")
    if _has_safety_risk(item):
        roles.append("toxicologist")
    if item.generation_summary:
        roles.append("computational_chemist")
    return _deduplicate(roles)


def _risk_questions(item: Any) -> list[str]:
    questions: list[str] = []
    for risk in item.risk_flags:
        cleaned = _safe_text(str(risk))
        if cleaned:
            questions.append(f"What evidence is needed to resolve {cleaned}?")
    return questions


def _key_hypothesis(item: Any) -> str:
    return (
        f"Review whether {item.candidate_name} has a defensible evidence rationale "
        f"for {item.disease_name} through {_target_text(item)}."
    )


def _evidence_summary(item: Any) -> dict[str, Any]:
    return {
        "target_evidence_count": item.evidence_summary.get("target_evidence_count"),
        "molecule_evidence_count": item.evidence_summary.get("molecule_evidence_count"),
        "literature_claim_counts": _safe_payload(
            item.evidence_summary.get("literature_claim_counts")
        ),
        "safety_warning_count": item.evidence_summary.get("safety_warning_count"),
        "developability_risk_level": item.evidence_summary.get(
            "developability_risk_level"
        ),
        "generated_score": item.evidence_summary.get("generated_score"),
        "priority_bucket": item.priority_bucket,
        "review_status": item.review_status,
        "experimental_results": _experimental_result_summary(item),
    }


def _experimental_result_summary(item: Any) -> dict[str, Any]:
    summary = item.evidence_summary.get("experimental_results")
    if not isinstance(summary, dict):
        return {"result_count": 0, "results": []}
    return _safe_payload(summary)


def _key_uncertainties(item: Any) -> list[str]:
    uncertainties: list[str] = []
    if item.candidate_origin == "generated":
        uncertainties.append(GENERATED_DIRECT_EVIDENCE_NOTICE)
    if not item.canonical_smiles:
        uncertainties.append("Structure metadata is missing.")
    if not item.target_symbols:
        uncertainties.append("No target symbols are available for this review item.")
    if item.confidence is None or item.confidence < 0.7:
        uncertainties.append("Model confidence is limited or unavailable.")
    if not item.literature_summary:
        uncertainties.append("Literature evidence may be missing or incomplete.")
    if item.risk_flags:
        uncertainties.append("Risk flags require expert triage before further handoff.")
    return uncertainties or ["No additional uncertainty notes were generated."]


def _artifact_paths(workspace: ReviewWorkspace, item: Any) -> dict[str, str]:
    paths: dict[str, str] = {}
    for metadata in (workspace.metadata, item.metadata):
        raw_paths = metadata.get("artifact_paths") if isinstance(metadata, dict) else None
        if isinstance(raw_paths, dict):
            paths.update(
                {
                    str(key): str(value)
                    for key, value in raw_paths.items()
                    if _safe_text(str(key))
                    and _safe_text(str(value))
                    and not should_omit_experimental_output_key(str(key))
                    and not should_omit_experimental_output_key(str(value))
                }
            )
    return paths


def _has_safety_risk(item: Any) -> bool:
    warning_count = int(item.evidence_summary.get("safety_warning_count") or 0)
    risk_text = " ".join([*item.risk_flags, *item.warnings]).lower()
    return warning_count > 0 or "safety" in risk_text or "tox" in risk_text


def _has_developability_risk(item: Any) -> bool:
    risk_level = str(
        item.evidence_summary.get("developability_risk_level")
        or item.developability_summary.get("risk_level")
        or ""
    ).lower()
    risk_text = " ".join(item.risk_flags).lower()
    return risk_level in {"medium", "high", "critical", "severe"} or "developability" in risk_text


def _target_text(item: Any) -> str:
    return ", ".join(item.target_symbols) if item.target_symbols else "the nominated targets"


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_payload(item)
            for key, item in value.items()
            if _safe_text(str(key)) and not should_omit_experimental_output_key(str(key))
        }
    if isinstance(value, list):
        return [_safe_payload(item) for item in value if _safe_payload(item) is not None]
    if isinstance(value, str):
        return _safe_text(value) or None
    return value


def _safe_text(value: str) -> str:
    value = sanitize_experimental_output_text(value)
    lowered = re.sub(r"[_\\/\-]+", " ", value.lower())
    if any(term in lowered for term in _FORBIDDEN_DETAIL_TERMS):
        return ""
    if any(token in lowered for token in ("°c", "37 c", "10 um", "step-by-step")):
        return ""
    return value


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _get_item(workspace: ReviewWorkspace, review_item_id: str):
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return item
    raise ValueError(f"Unknown review item: {review_item_id}")
