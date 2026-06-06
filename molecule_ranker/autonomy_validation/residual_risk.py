from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from molecule_ranker import __version__
from molecule_ranker.autonomy_validation.schemas import (
    AutonomyRiskLevel,
    AutonomyValidationSchema,
    ResidualRisk,
    ResidualRiskLikelihood,
)

RESIDUAL_RISK_JSON = "residual_risk_register.json"
RESIDUAL_RISK_MARKDOWN = "residual_risk_register.md"


class ResidualRiskRegister(AutonomyValidationSchema):
    register_id: str
    version: str
    scope: str
    risks: list[ResidualRisk] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)


def build_default_residual_risk_register(
    *,
    now: Callable[[], datetime] | None = None,
) -> ResidualRiskRegister:
    timestamp = now or (lambda: datetime.now(UTC))
    risks = _default_residual_risks()
    validate_residual_risk_register(risks)
    return ResidualRiskRegister(
        register_id=f"residual-risk-register-{uuid4().hex[:12]}",
        version=__version__,
        scope="v2_9_autonomy_validation_residual_risks",
        risks=risks,
        created_at=timestamp(),
        metadata={
            "output_files": [RESIDUAL_RISK_JSON, RESIDUAL_RISK_MARKDOWN],
            "risk_count": len(risks),
            "high_or_critical_risk_count": sum(
                1 for risk in risks if risk.severity in {"high", "critical"}
            ),
        },
    )


def write_residual_risk_register(
    output_dir: Path | str,
    *,
    now: Callable[[], datetime] | None = None,
) -> ResidualRiskRegister:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    register = build_default_residual_risk_register(now=now)
    (output_path / RESIDUAL_RISK_JSON).write_text(
        json.dumps(register.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_path / RESIDUAL_RISK_MARKDOWN).write_text(
        render_residual_risk_register_markdown(register),
        encoding="utf-8",
    )
    return register


def validate_residual_risk_register(risks: list[ResidualRisk]) -> None:
    for risk in risks:
        if risk.severity in {"high", "critical"} and not risk.mitigation.strip():
            raise ValueError(f"high or critical risk requires mitigation: {risk.risk_id}")
        if risk.status == "accepted":
            rationale = str(risk.metadata.get("acceptance_rationale", "")).strip()
            if not risk.owner_role or not rationale:
                raise ValueError(
                    f"accepted risk requires owner_role and acceptance_rationale: {risk.risk_id}"
                )


def render_residual_risk_register_markdown(register: ResidualRiskRegister) -> str:
    lines = [
        "# Residual Risk Register",
        "",
        f"- Register ID: {register.register_id}",
        f"- Version: {register.version}",
        f"- Scope: {register.scope}",
        f"- Created at: {register.created_at.isoformat()}",
        "",
        "This register records residual software/autonomy risks for V3.0 readiness. "
        "It is not clinical validation or regulatory safety certification.",
        "",
        "## Risks",
        "",
    ]
    for risk in register.risks:
        lines.extend(
            [
                f"### {risk.risk_id}",
                "",
                f"- Type: {risk.risk_type}",
                f"- Severity: {risk.severity}",
                f"- Likelihood: {risk.likelihood}",
                f"- Status: {risk.status}",
                f"- Owner role: {risk.owner_role or 'unassigned'}",
                f"- Mitigation: {risk.mitigation}",
                "",
                risk.description,
                "",
            ]
        )
    return "\n".join(lines)


def _default_residual_risks() -> list[ResidualRisk]:
    return [
        _risk(
            "risk_scientific_overclaim",
            "scientific_overclaim",
            "Users or agents may overstate computational or workflow outputs as scientific facts.",
            "high",
            "possible",
            (
                "Require forbidden-claim scanning, review gates, and explicit "
                "non-evidence limitations."
            ),
            "scientific_governance_lead",
        ),
        _risk(
            "risk_generated_asset_misuse",
            "generated_molecule_antibody_misuse",
            "Generated molecules or antibodies may be misused as validated candidates.",
            "high",
            "possible",
            (
                "Keep generated assets labeled as computational hypotheses and block "
                "advancement without review."
            ),
            "discovery_platform_owner",
        ),
        _risk(
            "risk_codex_prompt_injection",
            "codex_prompt_injection",
            "Prompt injection may attempt policy override, approval bypass, or secret disclosure.",
            "high",
            "possible",
            (
                "Run prompt-injection red-team fixtures and enforce tool, secret, "
                "and policy boundaries."
            ),
            "security_engineer",
        ),
        _risk(
            "risk_external_integration_misconfiguration",
            "external_integration_misconfiguration",
            (
                "External connectors may be deployed with incorrect write permissions "
                "or approval policy."
            ),
            "high",
            "possible",
            (
                "Default integrations to dry-run/read-only and require "
                "environment-specific write approval tests."
            ),
            "integration_owner",
        ),
        _risk(
            "risk_data_provenance_loss",
            "data_provenance_loss",
            "Artifact lineage can be incomplete if source records or transformations are omitted.",
            "medium",
            "possible",
            (
                "Require result-bundle lineage manifests and reproducibility checks "
                "before certification."
            ),
            "data_governance_owner",
        ),
        _risk(
            "risk_assay_result_mislinking",
            "assay_result_mislinking",
            (
                "Imported assay results can be linked to the wrong candidate, target, "
                "or external record."
            ),
            "medium",
            "possible",
            "Require exact imported result IDs, mapping review, and external record references.",
            "evidence_review_lead",
        ),
        _risk(
            "risk_failed_qc_misinterpretation",
            "failed_qc_misinterpretation",
            "Failed QC artifacts may be misread as positive or negative evidence.",
            "medium",
            "possible",
            "Quarantine failed QC and block use as evidence in certifications and dashboards.",
            "quality_owner",
        ),
        _risk(
            "risk_prediction_overreliance",
            "overreliance_on_docking_model_predictions",
            (
                "Users may over rely on docking or model predictions as if they were "
                "experimental evidence."
            ),
            "medium",
            "possible",
            "Separate model predictions from evidence and display computational-only limitations.",
            "model_governance_owner",
        ),
        _risk(
            "risk_incomplete_live_data",
            "incomplete_live_data",
            "Live read-only data can be incomplete, stale, unavailable, or partially mapped.",
            "medium",
            "likely",
            "Surface partial-data status and require live-data caveats in readiness reports.",
            "platform_operations",
        ),
        _risk(
            "risk_secret_exposure",
            "credential_secret_exposure",
            "Runtime agents or tools may be prompted to expose credentials or secrets.",
            "critical",
            "unlikely",
            (
                "Enforce secret redaction, blocked secret tools, and escalation on "
                "exfiltration attempts."
            ),
            "security_owner",
        ),
        _risk(
            "risk_excessive_autonomy",
            "excessive_autonomy",
            "Autonomous agents may act beyond intended governance or budget boundaries.",
            "high",
            "possible",
            "Constrain autonomy modes, require approval gates, and monitor budget violations.",
            "platform_governance_owner",
        ),
        _risk(
            "risk_user_misunderstanding",
            "user_misunderstanding",
            (
                "Users may misunderstand V3.0 validation artifacts as clinical or "
                "scientific validation."
            ),
            "medium",
            "possible",
            "Use prominent report limitations and review workflow labels.",
            "product_owner",
        ),
        _risk(
            "risk_dashboard_misinterpretation",
            "dashboard_misinterpretation",
            "Dashboard summaries may hide important limitations, lineage gaps, or residual risks.",
            "medium",
            "possible",
            "Include residual-risk and limitation sections in dashboard outputs.",
            "product_owner",
        ),
        _risk(
            "risk_model_calibration_limitations",
            "model_calibration_limitations",
            "Model confidence and ranking outputs may be poorly calibrated for some domains.",
            "medium",
            "possible",
            "Track calibration limitations and keep model predictions separate from evidence.",
            "model_governance_owner",
        ),
        _risk(
            "risk_biologics_sequence_uncertainty",
            "biologics_sequence_uncertainty",
            (
                "Biologics sequence annotations, numbering, or novelty checks may be "
                "uncertain or incomplete."
            ),
            "medium",
            "possible",
            "Require sequence validation, numbering, novelty, developability, and review gates.",
            "biologics_engineering_lead",
        ),
    ]


def _risk(
    risk_id: str,
    risk_type: str,
    description: str,
    severity: AutonomyRiskLevel,
    likelihood: ResidualRiskLikelihood,
    mitigation: str,
    owner_role: str,
) -> ResidualRisk:
    return ResidualRisk(
        risk_id=risk_id,
        risk_type=risk_type,
        description=description,
        severity=severity,
        likelihood=likelihood,
        mitigation=mitigation,
        owner_role=owner_role,
        status="open",
        metadata={},
    )


__all__ = [
    "RESIDUAL_RISK_JSON",
    "RESIDUAL_RISK_MARKDOWN",
    "ResidualRisk",
    "ResidualRiskRegister",
    "build_default_residual_risk_register",
    "render_residual_risk_register_markdown",
    "validate_residual_risk_register",
    "write_residual_risk_register",
]
