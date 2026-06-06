from __future__ import annotations

REQUIRED_GUARDRAILS: list[str] = [
    "no_medical_advice",
    "no_patient_treatment_guidance",
    "no_dosing_guidance",
    "no_lab_protocols",
    "no_synthesis_instructions",
    "no_expression_purification_immunization_or_wet_lab_protocols",
    "no_fabricated_evidence",
    "no_fabricated_assay_results",
    "no_fabricated_citations",
    "no_fabricated_molecules_or_antibody_sequences",
    "no_fabricated_graph_facts",
    "no_fabricated_external_records",
    "no_fabricated_approvals",
    "no_codex_generated_scientific_truth",
    "generated_assets_are_hypotheses_only",
    "approved_tools_only",
    "external_writes_require_human_approval",
    "result_bundles_are_software_autonomy_artifacts",
]

REQUIRED_HUMAN_GOVERNANCE_POINTS: list[str] = [
    "external_write_approval",
    "generated_molecule_review_before_advancement",
    "generated_antibody_review_before_advancement",
    "write_approved_live_mode_approval",
    "policy_override_approval",
    "capability_grant_approval",
    "autonomy_budget_approval",
    "agent_certification_approval",
    "high_risk_action_review",
    "result_bundle_certification_review",
]

FORBIDDEN_OUTPUTS: list[str] = [
    "no medical advice",
    "no patient treatment guidance",
    "no dosing guidance",
    "no lab protocols",
    "no synthesis instructions",
    "no expression, purification, immunization, or wet-lab protocols",
    "no fabricated evidence",
    "no fabricated assay results",
    "no fabricated citations",
    "no fabricated molecules or antibody sequences",
    "no fabricated graph facts",
    "no fabricated external records",
    "no fabricated approvals",
    "no Codex-generated scientific truth",
    (
        "no generated-molecule or generated-antibody claims of binding, activity, "
        "safety, efficacy, manufacturability, or therapeutic value"
    ),
]

__all__ = [
    "FORBIDDEN_OUTPUTS",
    "REQUIRED_GUARDRAILS",
    "REQUIRED_HUMAN_GOVERNANCE_POINTS",
]
