from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from molecule_ranker import __version__
from molecule_ranker.autonomy_validation.boundary_tests import (
    build_autonomy_boundary_fixtures,
)
from molecule_ranker.autonomy_validation.schemas import SafetyCaseReport

SAFETY_CASE_JSON = "v3_safety_case.json"
SAFETY_CASE_MARKDOWN = "v3_safety_case.md"

GLOBAL_LIMITATIONS = [
    "This is not a regulatory safety case.",
    "This is not clinical validation.",
    "This is autonomy and platform safety evidence for V3.0 readiness.",
    "No claim establishes binding, activity, safety, efficacy, manufacturability, "
    "therapeutic value, dosing, patient treatment, or laboratory readiness.",
]


def build_v3_safety_case_report(
    *,
    now: Callable[[], datetime] | None = None,
) -> SafetyCaseReport:
    timestamp = now or (lambda: datetime.now(UTC))
    boundary_ids = {
        fixture.boundary_type: fixture.fixture_id
        for fixture in build_autonomy_boundary_fixtures()
    }
    claims = _safety_claims(boundary_ids)
    residual_risk_ids = sorted(
        {
            risk_id
            for claim in claims
            for risk_id in claim["residual_risks"]
        }
    )
    evidence_artifact_ids = sorted(
        {
            artifact_id
            for claim in claims
            for artifact_id in claim["supporting_validation_artifacts"]
        }
    )
    return SafetyCaseReport(
        safety_case_id=f"v3-safety-case-{uuid4().hex[:12]}",
        version=__version__,
        scope="v2_9_autonomy_platform_safety_evidence_for_v3_readiness",
        claims=claims,
        evidence_artifact_ids=evidence_artifact_ids,
        residual_risks=residual_risk_ids,
        unresolved_findings=[],
        conclusion=(
            "V3.0 autonomy/platform safety claims are supported by deterministic "
            "software validation artifacts and boundary fixtures. This report is not "
            "regulatory, clinical, or scientific validation."
        ),
        created_at=timestamp(),
        metadata={
            "output_files": [SAFETY_CASE_JSON, SAFETY_CASE_MARKDOWN],
            "claim_count": len(claims),
            "boundary_fixture_count": len(boundary_ids),
            "limitations": GLOBAL_LIMITATIONS,
        },
    )


def write_v3_safety_case_report(
    output_dir: Path | str,
    *,
    now: Callable[[], datetime] | None = None,
) -> SafetyCaseReport:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report = build_v3_safety_case_report(now=now)
    (output_path / SAFETY_CASE_JSON).write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_path / SAFETY_CASE_MARKDOWN).write_text(
        render_v3_safety_case_markdown(report),
        encoding="utf-8",
    )
    return report


def render_v3_safety_case_markdown(report: SafetyCaseReport) -> str:
    lines = [
        "# V3 Safety Case",
        "",
        f"- Safety case ID: {report.safety_case_id}",
        f"- Version: {report.version}",
        f"- Scope: {report.scope}",
        f"- Created at: {report.created_at.isoformat()}",
        "",
        "This is autonomy/platform safety evidence. It is not a regulatory safety case, "
        "clinical validation, medical advice, or scientific validation.",
        "",
        "## Claims",
        "",
    ]
    for claim in report.claims:
        lines.extend(
            [
                f"### {claim['claim_id']}",
                "",
                claim["statement"],
                "",
                f"- Status: {'supported' if claim['supported'] else 'not supported'}",
                "- Supporting validation artifacts: "
                + ", ".join(claim["supporting_validation_artifacts"]),
                "- Boundary tests: " + ", ".join(claim["boundary_tests"]),
                "- Residual risks: " + ", ".join(claim["residual_risks"]),
                "- Limitations: " + " ".join(claim["limitations"]),
                "",
            ]
        )
    lines.extend(
        [
            "## Global Limitations",
            "",
            *[f"- {limitation}" for limitation in GLOBAL_LIMITATIONS],
            "",
            "## Conclusion",
            "",
            report.conclusion,
            "",
        ]
    )
    return "\n".join(lines)


def _safety_claims(boundary_ids: dict[str, str]) -> list[dict[str, Any]]:
    return [
        _claim(
            claim_id="codex_cannot_create_biomedical_evidence_directly",
            statement="Codex outputs are not accepted as biomedical evidence directly.",
            artifacts=[
                "AutonomyBoundarySuite:evidence_fabrication",
                "EndToEndResultCertification:codex_outputs_separate",
                "EndToEndResultCertification:exact_imported_evidence_rule",
            ],
            boundary_tests=[
                boundary_ids["evidence_fabrication"],
                boundary_ids["codex_self_certification"],
                boundary_ids["failed_qc_treated_as_evidence"],
            ],
            residual_risks=["risk_imported_record_quality", "risk_prompted_overclaim"],
            limitations=[
                "Imported source quality still depends on upstream validation and review."
            ],
        ),
        _claim(
            claim_id="codex_cannot_create_assay_results",
            statement=(
                "Codex cannot create assay results or convert generated text into "
                "assay evidence."
            ),
            artifacts=[
                "AutonomyBoundarySuite:assay_result_fabrication",
                "EndToEndResultCertification:failed_qc_not_treated_as_evidence",
            ],
            boundary_tests=[
                boundary_ids["assay_result_fabrication"],
                boundary_ids["failed_qc_treated_as_evidence"],
            ],
            residual_risks=["risk_imported_record_quality"],
            limitations=["The platform can only validate artifact boundaries, not assay truth."],
        ),
        _claim(
            claim_id="generated_assets_remain_computational_hypotheses",
            statement=(
                "Generated molecules and antibodies remain computational hypotheses "
                "unless separately imported and validated evidence exists."
            ),
            artifacts=[
                "AutonomyBoundarySuite:generated_advancement",
                "EndToEndResultCertification:generated_labels_intact",
                "EndToEndResultCertification:scientific_boundaries_passed",
            ],
            boundary_tests=[
                boundary_ids["molecule_fabrication"],
                boundary_ids["antibody_sequence_fabrication"],
                boundary_ids["generated_molecule_advancement_without_review"],
                boundary_ids["generated_antibody_advancement_without_review"],
            ],
            residual_risks=["risk_generated_asset_misinterpretation"],
            limitations=[
                "Computational hypotheses may still be misread outside governed UI context."
            ],
        ),
        _claim(
            claim_id="external_writes_require_approval",
            statement="External writes require explicit approval before execution.",
            artifacts=[
                "AutonomyBoundarySuite:external_write_without_approval",
                "EndToEndResultCertification:integration_boundaries_passed",
                "HumanGovernanceMatrix:write_approval_required",
            ],
            boundary_tests=[
                boundary_ids["external_write_without_approval"],
                boundary_ids["approval_bypass"],
                boundary_ids["codex_self_approval"],
            ],
            residual_risks=["risk_misconfigured_external_connector"],
            limitations=["Connector enforcement depends on correct deployment configuration."],
        ),
        _claim(
            claim_id="stage_gates_require_human_approval",
            statement="Stage gates and advancement decisions require governed human approval.",
            artifacts=[
                "AutonomyBoundarySuite:stage_gate_bypass",
                "HumanGovernanceMatrix:stage_gate_approval",
            ],
            boundary_tests=[
                boundary_ids["stage_gate_bypass"],
                boundary_ids["approval_bypass"],
                boundary_ids["generated_molecule_advancement_without_review"],
                boundary_ids["generated_antibody_advancement_without_review"],
            ],
            residual_risks=["risk_approval_process_quality"],
            limitations=["Human approvers remain responsible for decision quality."],
        ),
        _claim(
            claim_id="failed_qc_not_treated_as_evidence",
            statement="Failed QC artifacts are not treated as positive or negative evidence.",
            artifacts=[
                "AutonomyBoundarySuite:failed_qc_treated_as_evidence",
                "EndToEndResultCertification:failed_qc_not_treated_as_evidence",
            ],
            boundary_tests=[
                boundary_ids["failed_qc_treated_as_evidence"],
                boundary_ids["evidence_fabrication"],
            ],
            residual_risks=["risk_qc_metadata_incomplete"],
            limitations=["The rule depends on QC status metadata being available and preserved."],
        ),
        _claim(
            claim_id="tool_use_restricted_to_approved_tools",
            statement="Tool use is restricted to approved tools and permitted artifacts.",
            artifacts=[
                "AutonomyBoundarySuite:unauthorized_tool",
                "AgentReliabilityScorecard:tool_success_rate",
                "AgentReliabilityScorecard:policy_violation_rate",
            ],
            boundary_tests=[
                boundary_ids["unauthorized_tool"],
                boundary_ids["unauthorized_artifact"],
                boundary_ids["secret_exfiltration"],
            ],
            residual_risks=["risk_tool_registry_configuration"],
            limitations=[
                "Tool registry and artifact permissions must be maintained operationally."
            ],
        ),
        _claim(
            claim_id="runtime_agents_respect_governance_and_budgets",
            statement=(
                "Runtime agents are evaluated for governance, approval, safety, "
                "and budget behavior."
            ),
            artifacts=[
                "AgentReliabilityScorecard:RuntimeAgent",
                "AgentReliabilityScorecard:GuardrailSentinel",
                "AgentReliabilityScorecard:budget_violation_rate",
            ],
            boundary_tests=[
                boundary_ids["policy_override"],
                boundary_ids["approval_bypass"],
                boundary_ids["external_write_without_approval"],
            ],
            residual_risks=["risk_runtime_observability_gap"],
            limitations=["Scorecards require complete runtime observations to avoid unknown risk."],
        ),
        _claim(
            claim_id="red_team_boundaries_detect_unsafe_outputs",
            statement=(
                "Red-team boundary tests detect unsafe outputs and unsafe events "
                "in fixtures."
            ),
            artifacts=[
                "AutonomyBoundarySuite:all_boundary_fixtures",
                "AutonomyBoundarySuite:unsafe_action_escape_rate",
                "AutonomyBoundarySuite:fabricated_scientific_truth_escape_rate",
            ],
            boundary_tests=[
                boundary_ids["medical_advice"],
                boundary_ids["lab_protocol"],
                boundary_ids["synthesis_instruction"],
                boundary_ids["dosing_patient_guidance"],
                boundary_ids["expression_purification_immunization_protocol"],
            ],
            residual_risks=["risk_fixture_coverage_gap"],
            limitations=["Fixture tests do not prove every possible prompt or deployment path."],
        ),
        _claim(
            claim_id="result_bundles_preserve_lineage_and_limitations",
            statement="Result bundles preserve lineage, reproducibility context, and limitations.",
            artifacts=[
                "EndToEndResultCertification:lineage_complete",
                "EndToEndResultCertification:reproducibility_manifest_valid",
                "EndToEndResultCertification:limitations_present",
            ],
            boundary_tests=[
                boundary_ids["codex_self_certification"],
                boundary_ids["external_record_fabrication"],
                boundary_ids["citation_fabrication"],
            ],
            residual_risks=["risk_lineage_source_incomplete"],
            limitations=["Lineage can only preserve records supplied to the platform."],
        ),
    ]


def _claim(
    *,
    claim_id: str,
    statement: str,
    artifacts: list[str],
    boundary_tests: list[str],
    residual_risks: list[str],
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "statement": statement,
        "supported": bool(artifacts and boundary_tests),
        "supporting_validation_artifacts": artifacts,
        "boundary_tests": boundary_tests,
        "residual_risks": residual_risks,
        "limitations": limitations,
    }


__all__ = [
    "SAFETY_CASE_JSON",
    "SAFETY_CASE_MARKDOWN",
    "SafetyCaseReport",
    "build_v3_safety_case_report",
    "render_v3_safety_case_markdown",
    "write_v3_safety_case_report",
]
