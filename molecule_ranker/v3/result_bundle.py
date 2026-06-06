from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.v3.product_contract import (
    V3_PRODUCT_CONTRACT_VERSION,
    v3_product_contract_payload,
)

V3_BUNDLE_FILENAMES = (
    "v3_result_bundle.json",
    "v3_result_bundle.md",
    "v3_result_bundle.zip",
)
V3ResultBundleMode = Literal[
    "mocked",
    "dry_run",
    "read_only_live",
    "write_approved_live",
]

FORBIDDEN_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bvalidated\s+binder\b", re.I), "validated binder claim"),
    (re.compile(r"\bproven\s+efficacy\b", re.I), "efficacy claim"),
    (re.compile(r"\bproven\s+safety\b", re.I), "safety claim"),
    (re.compile(r"\bsafe\s+and\s+effective\b", re.I), "safety/efficacy claim"),
    (re.compile(r"\bhas\s+binding\b|\bbinds?\s+(?:to\s+)?target\b", re.I), "binding claim"),
    (re.compile(r"\btherapeutic\s+value\b", re.I), "therapeutic value claim"),
    (re.compile(r"\btreats?\b|\bcures?\b", re.I), "treatment claim"),
    (re.compile(r"\bis\s+clinical\s+validation\b", re.I), "clinical validation claim"),
    (re.compile(r"\bclinical\s+validation\s+confirmed\b", re.I), "clinical validation claim"),
    (re.compile(r"\bis\s+biomedical\s+evidence\b", re.I), "biomedical evidence claim"),
    (re.compile(r"\bdosing\s+regimen\b", re.I), "dosing guidance"),
    (re.compile(r"\bsynthesis\s+route\b", re.I), "synthesis instruction"),
    (re.compile(r"\blab\s+protocol\s+steps?\b", re.I), "lab protocol"),
)
SEPARATION_METADATA_KEYS = (
    "evidence_sections",
    "prediction_sections",
    "review_sections",
    "codex_sections",
    "graph_sections",
    "generated_sections",
    "evaluation_sections",
)


class V3ResultBundle(BaseModel):
    bundle_id: str
    product_version: str
    product_contract_version: str
    workflow_id: str
    project_id: str | None
    disease_name: str | None
    mode: V3ResultBundleMode
    created_at: datetime
    executive_summary: dict[str, Any]
    candidate_summary: dict[str, Any]
    generated_molecule_summary: dict[str, Any]
    biologics_summary: dict[str, Any]
    evidence_summary: dict[str, Any]
    literature_summary: dict[str, Any]
    developability_summary: dict[str, Any]
    experimental_evidence_summary: dict[str, Any]
    model_prediction_summary: dict[str, Any]
    structure_summary: dict[str, Any]
    graph_summary: dict[str, Any]
    hypothesis_summary: dict[str, Any]
    portfolio_summary: dict[str, Any]
    campaign_summary: dict[str, Any]
    review_summary: dict[str, Any]
    evaluation_summary: dict[str, Any]
    integration_summary: dict[str, Any]
    codex_agent_summary: dict[str, Any]
    governance_summary: dict[str, Any]
    approval_summary: dict[str, Any]
    lineage_summary: dict[str, Any]
    validation_summary: dict[str, Any]
    limitations: list[str] = Field(min_length=1)
    required_next_human_decisions: list[str]
    artifact_manifest: list[dict[str, Any]]
    contract_validation: dict[str, Any]
    guardrail_validation: dict[str, Any]
    metadata: dict[str, Any]

    @field_validator("created_at")
    @classmethod
    def require_timezone_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def enforce_v3_bundle_rules(self) -> V3ResultBundle:
        issues: list[str] = []
        if self.product_version != "3.0.0":
            issues.append("product_version must be 3.0.0")
        if self.product_contract_version != V3_PRODUCT_CONTRACT_VERSION:
            issues.append(
                f"product_contract_version must be {V3_PRODUCT_CONTRACT_VERSION}"
            )
        limitation_text = " ".join(self.limitations).lower()
        if "research-planning result" not in limitation_text:
            issues.append("limitations must state bundle is a research-planning result")
        if "not biomedical evidence" not in limitation_text:
            issues.append("limitations must state bundle is not biomedical evidence")
        if "not clinical validation" not in limitation_text:
            issues.append("limitations must include not clinical validation")
        if not _has_no_lab_synthesis_dosing_disclaimer(limitation_text):
            issues.append("limitations must include no lab protocol/synthesis/dosing disclaimer")
        missing_separation = [
            key for key in SEPARATION_METADATA_KEYS if key not in self.metadata
        ]
        if missing_separation:
            issues.append("metadata missing separation keys: " + ", ".join(missing_separation))
        if self.contract_validation.get("sections_separated") is not True:
            issues.append("contract_validation must confirm sections_separated")
        if self.guardrail_validation.get("forbidden_claims_absent") is not True:
            issues.append("guardrail_validation must confirm forbidden_claims_absent")
        forbidden = _forbidden_claims(self)
        if forbidden:
            issues.append("forbidden V3 bundle claim: " + ", ".join(forbidden))
        if issues:
            raise ValueError("; ".join(issues))
        return self


def build_v3_result_bundle(
    *,
    e2e_bundle: Any,
    validation_summary: dict[str, Any],
    artifact_manifest: list[dict[str, Any]],
    codex_agent_summary: dict[str, Any],
    governance_summary: dict[str, Any],
    approval_summary: dict[str, Any],
    lineage_summary: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> V3ResultBundle:
    product_contract = v3_product_contract_payload()
    return V3ResultBundle(
        bundle_id=f"v3-{e2e_bundle.bundle_id}",
        product_version=product_contract["product_version"],
        product_contract_version=product_contract["product_contract_version"],
        workflow_id=e2e_bundle.workflow_id,
        project_id=e2e_bundle.project_id,
        disease_name=e2e_bundle.disease_name,
        mode=e2e_bundle.metadata.get("mode", "dry_run"),
        created_at=e2e_bundle.created_at,
        executive_summary={
            "summary": e2e_bundle.result_summary,
            "scope": "internal research planning",
            "not_biomedical_evidence": True,
            "not_clinical_validation": True,
        },
        candidate_summary=e2e_bundle.candidate_summary,
        generated_molecule_summary={
            **e2e_bundle.generated_summary,
            "label": "computational_hypotheses_only",
            "advanced_without_review": False,
            "review_required": True,
        },
        biologics_summary={
            **e2e_bundle.biologics_summary,
            "label": (
                "computational_hypotheses_only"
                if e2e_bundle.biologics_summary.get("antibody_generation_enabled")
                is True
                else e2e_bundle.biologics_summary.get("label")
            ),
            "generated_antibody_warning": e2e_bundle.biologics_summary.get(
                "generated_antibody_warning",
                "Generated antibodies are computational hypotheses only.",
            ),
        },
        evidence_summary={
            "section_boundary": "source_backed_evidence_only",
            **e2e_bundle.evidence_summary,
        },
        literature_summary={
            "section_boundary": "literature_evidence_separate_from_predictions",
            "literature_steps_completed": e2e_bundle.evidence_summary.get(
                "literature_steps_completed", 0
            ),
        },
        developability_summary={
            "section_boundary": "developability_triage_not_manufacturability_claim",
            **e2e_bundle.review_summary,
        },
        experimental_evidence_summary={
            "section_boundary": "imported_validated_records_only",
            "experimental_evidence": e2e_bundle.evidence_summary.get(
                "experimental_evidence", {}
            ),
        },
        model_prediction_summary={
            "section_boundary": "model_predictions_not_evidence",
            "model_predictions_are_separate": True,
        },
        structure_summary={
            "section_boundary": "structure_assessments_not_docking_expansion",
            "structure_outputs_are_separate": True,
        },
        graph_summary={
            "section_boundary": "graph_inference_not_graph_fact",
            "graph_inferences_are_separate": True,
        },
        hypothesis_summary={
            "section_boundary": "hypotheses_for_human_review",
            "generated_hypotheses_are_separate": True,
        },
        portfolio_summary={
            "section_boundary": "portfolio_planning_not_approval",
            **e2e_bundle.campaign_summary,
        },
        campaign_summary={
            "section_boundary": "campaign_plan_not_activation",
            "activated": False,
            **e2e_bundle.campaign_summary,
        },
        review_summary={
            "section_boundary": "human_review_workspace",
            **e2e_bundle.review_summary,
        },
        evaluation_summary={
            "section_boundary": "software_workflow_evaluation",
            "clinical_validation": False,
            "scientific_validation": False,
            **e2e_bundle.evaluation_summary,
        },
        integration_summary=e2e_bundle.integration_summary,
        codex_agent_summary=codex_agent_summary,
        governance_summary=governance_summary,
        approval_summary=approval_summary,
        lineage_summary=lineage_summary,
        validation_summary=validation_summary,
        limitations=_v3_limitations(e2e_bundle.limitations),
        required_next_human_decisions=[
            "Review generated hypotheses before advancement.",
            "Approve any external write before write_approved_live execution.",
            "Certify result bundle before campaign activation.",
        ],
        artifact_manifest=artifact_manifest,
        contract_validation={
            "product_contract_valid": validation_summary.get("metadata", {})
            .get("checks", {})
            .get("v3_product_contract_valid", False),
            "sections_separated": True,
            "contract_version": product_contract["product_contract_version"],
        },
        guardrail_validation={
            "forbidden_claims_absent": True,
            "generated_hypotheses_labeled": True,
            "not_clinical_validation_disclaimer_present": True,
            "no_lab_protocol_synthesis_dosing_disclaimer_present": True,
        },
        metadata={
            **_separation_metadata(),
            "product_contract": product_contract,
            **(metadata or {}),
        },
    )


def write_v3_result_bundle(
    bundle: V3ResultBundle,
    *,
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "v3_result_bundle.json"
    markdown_path = output_dir / "v3_result_bundle.md"
    zip_path = output_dir / "v3_result_bundle.zip"

    json_path.write_text(
        json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_v3_result_bundle_markdown(bundle), encoding="utf-8")
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(json_path, arcname=json_path.name)
        archive.write(markdown_path, arcname=markdown_path.name)
    return {
        "v3_result_bundle.json": str(json_path),
        "v3_result_bundle.md": str(markdown_path),
        "v3_result_bundle.zip": str(zip_path),
    }


def render_v3_result_bundle_markdown(bundle: V3ResultBundle) -> str:
    lines = [
        f"# V3 Result Bundle: {bundle.workflow_id}",
        "",
        f"- Product version: `{bundle.product_version}`",
        f"- Mode: `{bundle.mode}`",
        f"- Project: `{bundle.project_id}`",
        f"- Disease: `{bundle.disease_name}`",
        "",
        "## Executive Summary",
        "",
        json.dumps(bundle.executive_summary, sort_keys=True),
        "",
        "## Required Next Human Decisions",
        "",
        *[f"- {decision}" for decision in bundle.required_next_human_decisions],
        "",
        "## Limitations",
        "",
        *[f"- {limitation}" for limitation in bundle.limitations],
        "",
    ]
    return "\n".join(lines)


def _v3_limitations(existing: list[str]) -> list[str]:
    required = [
        "This bundle is a research-planning result, not biomedical evidence.",
        "This bundle is not clinical validation.",
        "No lab protocol, synthesis, or dosing guidance is provided.",
        "Generated molecules and antibodies are computational hypotheses only.",
        "Codex outputs are separated from evidence and require human review.",
    ]
    seen: set[str] = set()
    output: list[str] = []
    for limitation in [*existing, *required]:
        key = limitation.lower()
        if key not in seen:
            seen.add(key)
            output.append(limitation)
    return output


def _separation_metadata() -> dict[str, list[str]]:
    return {
        "evidence_sections": [
            "evidence_summary",
            "literature_summary",
            "experimental_evidence_summary",
        ],
        "prediction_sections": ["model_prediction_summary", "structure_summary"],
        "review_sections": ["review_summary", "developability_summary"],
        "codex_sections": ["codex_agent_summary"],
        "graph_sections": ["graph_summary"],
        "generated_sections": ["generated_molecule_summary", "hypothesis_summary"],
        "evaluation_sections": ["evaluation_summary"],
    }


def _has_no_lab_synthesis_dosing_disclaimer(text: str) -> bool:
    return "no lab protocol" in text and "synthesis" in text and "dosing" in text


def _forbidden_claims(bundle: V3ResultBundle) -> list[str]:
    payload = bundle.model_dump(mode="json")
    payload.pop("limitations", None)
    payload.pop("guardrail_validation", None)
    payload.pop("contract_validation", None)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("product_contract", None)
    text = json.dumps(payload, sort_keys=True)
    return [
        label
        for pattern, label in FORBIDDEN_CLAIM_PATTERNS
        if pattern.search(text)
    ]


__all__ = [
    "V3_BUNDLE_FILENAMES",
    "V3ResultBundle",
    "build_v3_result_bundle",
    "render_v3_result_bundle_markdown",
    "write_v3_result_bundle",
]
