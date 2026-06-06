from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.v3.result_bundle import V3ResultBundle
from molecule_ranker.v3.workflow_contract import validate_v3_workflow

V3CertificationLevel = Literal[
    "mocked_validated",
    "dry_run_validated",
    "read_only_live_validated",
    "write_approved_live_validated",
    "failed",
]

FORBIDDEN_CERTIFICATION_TEXT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bvalidated\s+binder\b", re.I), "unsupported binding claim"),
    (re.compile(r"\bproven\s+efficacy\b|\befficacy\s+confirmed\b", re.I), "efficacy claim"),
    (re.compile(r"\bproven\s+safety\b|\bsafe\s+and\s+effective\b", re.I), "safety claim"),
    (re.compile(r"\btreats?\b|\bcures?\b", re.I), "treatment claim"),
    (re.compile(r"\btherapeutic\s+value\b", re.I), "therapeutic value claim"),
    (re.compile(r"\bdosing\s+regimen\b", re.I), "dosing guidance"),
    (re.compile(r"\bsynthesis\s+route\b", re.I), "synthesis instruction"),
    (re.compile(r"\blab\s+protocol\s+steps?\b", re.I), "lab protocol"),
)
REQUIRED_CHECKS: tuple[str, ...] = (
    "product_contract_included",
    "workflow_contract_valid",
    "required_artifacts_present",
    "artifact_contracts_valid",
    "lineage_complete",
    "guardrails_passed",
    "human_approvals_satisfied",
    "external_writes_absent_or_approved",
    "codex_outputs_separate_from_evidence",
    "generated_molecules_labeled",
    "generated_antibodies_labeled",
    "exact_imported_evidence_rule_enforced",
    "failed_qc_not_treated_as_evidence",
    "model_predictions_separate",
    "docking_scores_separate",
    "graph_inference_separate",
    "evaluation_outputs_separate",
    "no_forbidden_text",
    "reproducibility_manifest_valid",
    "safety_case_link_included",
)


class V3ResultCertification(BaseModel):
    certification_id: str
    bundle_id: str
    workflow_id: str
    project_id: str | None
    product_version: str
    product_contract_version: str
    certification_level: V3CertificationLevel
    certified: bool
    checks: dict[str, bool]
    findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    certified_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("certified_at")
    @classmethod
    def require_timezone_aware_certified_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("certified_at must be timezone-aware")
        return value


def certify_v3_result_bundle(
    bundle: V3ResultBundle,
    *,
    now: Callable[[], datetime] | None = None,
) -> V3ResultCertification:
    timestamp = now or (lambda: datetime.now(UTC))
    findings: list[str] = []
    checks = {
        "product_contract_included": _product_contract_included(bundle, findings),
        "workflow_contract_valid": _workflow_contract_valid(bundle, findings),
        "required_artifacts_present": _required_artifacts_present(bundle, findings),
        "artifact_contracts_valid": _artifact_contracts_valid(bundle, findings),
        "lineage_complete": _lineage_complete(bundle, findings),
        "guardrails_passed": _guardrails_passed(bundle, findings),
        "human_approvals_satisfied": _human_approvals_satisfied(bundle, findings),
        "external_writes_absent_or_approved": _external_writes_absent_or_approved(
            bundle, findings
        ),
        "codex_outputs_separate_from_evidence": _codex_outputs_separate(bundle, findings),
        "generated_molecules_labeled": _generated_molecules_labeled(bundle, findings),
        "generated_antibodies_labeled": _generated_antibodies_labeled(bundle, findings),
        "exact_imported_evidence_rule_enforced": _exact_imported_evidence_rule_enforced(
            bundle, findings
        ),
        "failed_qc_not_treated_as_evidence": _failed_qc_not_treated_as_evidence(
            bundle, findings
        ),
        "model_predictions_separate": _model_predictions_separate(bundle, findings),
        "docking_scores_separate": _docking_scores_separate(bundle, findings),
        "graph_inference_separate": _graph_inference_separate(bundle, findings),
        "evaluation_outputs_separate": _evaluation_outputs_separate(bundle, findings),
        "no_forbidden_text": _no_forbidden_text(bundle, findings),
        "reproducibility_manifest_valid": _reproducibility_manifest_valid(
            bundle, findings
        ),
        "safety_case_link_included": _safety_case_link_included(bundle, findings),
    }
    missing_check_names = sorted(set(REQUIRED_CHECKS) - set(checks))
    for name in missing_check_names:
        checks[name] = False
        findings.append(f"certification check missing: {name}")
    certified = all(checks.values())
    return V3ResultCertification(
        certification_id=f"v3-result-cert-{uuid4().hex[:16]}",
        bundle_id=bundle.bundle_id,
        workflow_id=bundle.workflow_id,
        project_id=bundle.project_id,
        product_version=bundle.product_version,
        product_contract_version=bundle.product_contract_version,
        certification_level=_certification_level(bundle.mode) if certified else "failed",
        certified=certified,
        checks=checks,
        findings=sorted(set(findings)),
        limitations=[
            "Certification is platform/workflow certification only.",
            "Certification is not clinical validation.",
            "Certification is not biomedical evidence, medical advice, treatment guidance, "
            "dosing guidance, lab protocol, or synthesis instruction.",
        ],
        certified_at=timestamp(),
        metadata={
            "certification_artifacts": [
                "v3_result_certification.json",
                "v3_result_certification.md",
            ],
            "required_check_count": len(REQUIRED_CHECKS),
            "failed_checks": [name for name, passed in checks.items() if not passed],
        },
    )


def write_v3_result_certification(
    certification: V3ResultCertification,
    *,
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "v3_result_certification.json"
    markdown_path = output_dir / "v3_result_certification.md"
    json_path.write_text(
        json.dumps(certification.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_v3_result_certification_markdown(certification),
        encoding="utf-8",
    )
    return {
        "v3_result_certification.json": str(json_path),
        "v3_result_certification.md": str(markdown_path),
    }


def render_v3_result_certification_markdown(
    certification: V3ResultCertification,
) -> str:
    lines = [
        f"# V3 Result Certification: {certification.workflow_id}",
        "",
        f"- Level: `{certification.certification_level}`",
        f"- Certified: `{certification.certified}`",
        f"- Product version: `{certification.product_version}`",
        "",
        "## Checks",
        "",
        *[
            f"- [{'x' if passed else ' '}] {name}"
            for name, passed in sorted(certification.checks.items())
        ],
        "",
        "## Findings",
        "",
    ]
    lines.extend(f"- {finding}" for finding in certification.findings or ["None"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in certification.limitations)
    lines.append("")
    return "\n".join(lines)


def _certification_level(mode: str) -> V3CertificationLevel:
    levels: dict[str, V3CertificationLevel] = {
        "mocked": "mocked_validated",
        "dry_run": "dry_run_validated",
        "read_only_live": "read_only_live_validated",
        "write_approved_live": "write_approved_live_validated",
    }
    return levels.get(mode, "failed")


def _product_contract_included(
    bundle: V3ResultBundle,
    findings: list[str],
) -> bool:
    contract = bundle.metadata.get("product_contract")
    if not isinstance(contract, dict):
        findings.append("product contract missing")
        return False
    if contract.get("product_version") != bundle.product_version:
        findings.append("product contract version mismatch")
        return False
    if contract.get("product_contract_version") != bundle.product_contract_version:
        findings.append("product contract identifier mismatch")
        return False
    return True


def _workflow_contract_valid(bundle: V3ResultBundle, findings: list[str]) -> bool:
    validation = validate_v3_workflow(
        {"workflow_type": "full_discovery_loop", "mode": bundle.mode}
    )
    if not validation.valid:
        findings.extend(f"workflow contract invalid: {issue}" for issue in validation.issues)
        return False
    if not _validation_check(bundle, "v3_product_contract_valid", default=True):
        findings.append("workflow validation did not confirm V3 product contract")
        return False
    return True


def _required_artifacts_present(bundle: V3ResultBundle, findings: list[str]) -> bool:
    filenames = {str(item.get("filename")) for item in bundle.artifact_manifest}
    required = {"candidates.json", "e2e_lineage.json", "e2e_validation.json"}
    missing = sorted(required - filenames)
    if missing:
        findings.append("required artifacts missing: " + ", ".join(missing))
        return False
    if bundle.validation_summary.get("required_artifacts_present") is False:
        findings.append("validation reported missing required artifacts")
        return False
    return True


def _artifact_contracts_valid(bundle: V3ResultBundle, findings: list[str]) -> bool:
    invalid = [
        str(item.get("artifact_id") or item.get("filename"))
        for item in bundle.artifact_manifest
        if item.get("contract_valid") is False
    ]
    if invalid:
        findings.append("artifact contracts invalid: " + ", ".join(invalid))
        return False
    if bundle.validation_summary.get("artifact_contracts_valid") is False:
        findings.append("validation reported invalid artifact contracts")
        return False
    return True


def _lineage_complete(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if int(bundle.lineage_summary.get("lineage_record_count", 0) or 0) <= 0:
        findings.append("lineage summary missing records")
        return False
    if bundle.validation_summary.get("lineage_complete") is False:
        findings.append("validation reported incomplete lineage")
        return False
    return True


def _guardrails_passed(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.validation_summary.get("guardrails_passed") is False:
        findings.append("validation reported guardrail failure")
        return False
    if bundle.guardrail_validation.get("forbidden_claims_absent") is not True:
        findings.append("guardrail validation did not confirm forbidden claims absent")
        return False
    return True


def _human_approvals_satisfied(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.validation_summary.get("approvals_satisfied") is False:
        findings.append("human approvals not satisfied")
        return False
    if bundle.mode == "write_approved_live" and _external_writes_performed(bundle):
        if not bundle.approval_summary.get("approval_ids"):
            findings.append("write-approved external write missing human approval")
            return False
    return True


def _external_writes_absent_or_approved(
    bundle: V3ResultBundle,
    findings: list[str],
) -> bool:
    writes = _external_writes_performed(bundle)
    if writes <= 0:
        return True
    if bundle.mode == "write_approved_live" and bundle.approval_summary.get("approval_ids"):
        return True
    findings.append("external writes present without approval")
    return False


def _codex_outputs_separate(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.codex_agent_summary.get("codex_outputs_are_separate") is True:
        return True
    findings.append("Codex outputs are not separated from evidence")
    return False


def _generated_molecules_labeled(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.generated_molecule_summary.get("label") == "computational_hypotheses_only":
        return True
    findings.append("generated molecules are not labeled as computational hypotheses")
    return False


def _generated_antibodies_labeled(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.biologics_summary.get("antibody_generation_enabled") is not True:
        return True
    valid_labels = {
        "computational_hypotheses_only",
        "Generated antibodies are computational hypotheses only.",
    }
    values = {
        str(bundle.biologics_summary.get("label", "")),
        str(bundle.biologics_summary.get("generated_antibody_warning", "")),
    }
    if values & valid_labels:
        return True
    findings.append("generated antibodies are not labeled as computational hypotheses")
    return False


def _exact_imported_evidence_rule_enforced(
    bundle: V3ResultBundle,
    findings: list[str],
) -> bool:
    direct_evidence = int(
        bundle.biologics_summary.get("generated_antibodies_with_direct_evidence", 0) or 0
    )
    exact_ids = bundle.biologics_summary.get("exact_imported_experimental_result_ids")
    if direct_evidence and not exact_ids:
        findings.append("generated antibody direct evidence lacks exact imported evidence")
        return False
    return True


def _failed_qc_not_treated_as_evidence(
    bundle: V3ResultBundle,
    findings: list[str],
) -> bool:
    payload = bundle.model_dump(mode="json")
    if _truthy_key(payload, "failed_qc_treated_as_evidence") or _truthy_key(
        payload, "failed_qc_treated_as_support"
    ):
        findings.append("failed QC treated as evidence")
        return False
    return True


def _model_predictions_separate(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if (
        bundle.model_prediction_summary.get("model_predictions_are_separate") is True
        and "model_prediction_summary" in bundle.metadata.get("prediction_sections", [])
    ):
        return True
    findings.append("model predictions are not separated")
    return False


def _docking_scores_separate(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.structure_summary.get("docking_scores_used_as_evidence") is True:
        findings.append("docking scores treated as evidence")
        return False
    if (
        bundle.structure_summary.get("docking_scores_are_separate") is True
        or "structure_summary" in bundle.metadata.get("prediction_sections", [])
    ):
        return True
    findings.append("docking scores are not separated")
    return False


def _graph_inference_separate(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if (
        bundle.graph_summary.get("graph_inferences_are_separate") is True
        and "graph_summary" in bundle.metadata.get("graph_sections", [])
    ):
        return True
    findings.append("graph inference is not separated")
    return False


def _evaluation_outputs_separate(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if (
        bundle.evaluation_summary.get("evaluation_outputs_are_separate") is True
        or "evaluation_summary" in bundle.metadata.get("evaluation_sections", [])
    ):
        return True
    findings.append("evaluation outputs are not separated")
    return False


def _no_forbidden_text(bundle: V3ResultBundle, findings: list[str]) -> bool:
    payload = bundle.model_dump(mode="json")
    payload.pop("limitations", None)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("product_contract", None)
    text = json.dumps(payload, sort_keys=True)
    labels = [
        label for pattern, label in FORBIDDEN_CERTIFICATION_TEXT if pattern.search(text)
    ]
    if labels:
        findings.append("forbidden text present: " + ", ".join(sorted(set(labels))))
        return False
    return True


def _reproducibility_manifest_valid(
    bundle: V3ResultBundle,
    findings: list[str],
) -> bool:
    manifest = bundle.metadata.get("reproducibility_manifest")
    if not isinstance(manifest, dict):
        findings.append("reproducibility manifest missing")
        return False
    if int(manifest.get("lineage_record_count", 0) or 0) <= 0:
        findings.append("reproducibility manifest missing lineage records")
        return False
    return True


def _safety_case_link_included(bundle: V3ResultBundle, findings: list[str]) -> bool:
    if bundle.metadata.get("safety_case_link"):
        return True
    findings.append("safety case link missing")
    return False


def _validation_check(bundle: V3ResultBundle, key: str, *, default: bool) -> bool:
    checks = bundle.validation_summary.get("metadata", {}).get("checks", {})
    if not isinstance(checks, dict):
        return default
    return bool(checks.get(key, default))


def _external_writes_performed(bundle: V3ResultBundle) -> int:
    return int(bundle.integration_summary.get("external_writes_performed", 0) or 0)


def _truthy_key(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        for key, raw in value.items():
            if str(key) == target and raw is True:
                return True
            if _truthy_key(raw, target):
                return True
    if isinstance(value, list):
        return any(_truthy_key(item, target) for item in value)
    return False


__all__ = [
    "REQUIRED_CHECKS",
    "V3CertificationLevel",
    "V3ResultCertification",
    "certify_v3_result_bundle",
    "render_v3_result_certification_markdown",
    "write_v3_result_certification",
]
