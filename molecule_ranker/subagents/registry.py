from __future__ import annotations

from collections.abc import Iterable

from molecule_ranker.subagents.schemas import SubagentProfile

DEFAULT_CONTEXT_BYTES = 64_000
HIGH_RISK_TOOL_CATEGORIES = [
    "external_write",
    "stage_gate",
    "campaign_advance",
    "generated_molecule_export",
    "destructive_action",
]
EVIDENCE_CREATING_TOOL_CATEGORIES = [
    "evidence_creation",
    "citation_creation",
    "assay_result_creation",
    "graph_fact_creation",
]


class SubagentRegistry:
    def __init__(self, profiles: Iterable[SubagentProfile] | None = None) -> None:
        self._profiles = {
            profile.subagent_id: profile
            for profile in profiles or builtin_subagent_profiles()
        }

    def list_profiles(self) -> list[SubagentProfile]:
        return sorted(self._profiles.values(), key=lambda profile: profile.subagent_id)

    def get(self, subagent_id: str) -> SubagentProfile | None:
        return self._profiles.get(subagent_id)

    def require(self, subagent_id: str) -> SubagentProfile:
        profile = self.get(subagent_id)
        if profile is None:
            raise KeyError(f"unknown subagent profile: {subagent_id}")
        return profile

    def by_role(self, role: str) -> SubagentProfile:
        for profile in self._profiles.values():
            if profile.role == role:
                return profile
        raise KeyError(f"unknown subagent role: {role}")


def builtin_subagent_profiles() -> list[SubagentProfile]:
    return [
        ProgramManagerSubagent,
        EvidenceReviewerSubagent,
        MoleculeDesignerSubagent,
        BiologicsEngineerSubagent,
        DevelopabilitySafetySubagent,
        ExperimentAnalystSubagent,
        PredictiveModelerSubagent,
        StructureReviewerSubagent,
        GraphReasonerSubagent,
        HypothesisPlannerSubagent,
        PortfolioStrategistSubagent,
        CampaignPlannerSubagent,
        IntegrationOperatorSubagent,
        EvaluationValidatorSubagent,
        GuardrailSentinelSubagent,
        PlatformOperatorSubagent,
    ]


def _profile(
    *,
    subagent_id: str,
    name: str,
    role: str,
    description: str,
    allowed: list[str],
    denied: list[str],
    permissions: list[str],
    autonomy: str = "suggest_only",
    can_delegate: bool = False,
    can_request_approval: bool = True,
    can_execute_tools: bool = True,
    can_write_artifacts: bool = True,
    guardrail_profile: str,
    responsibilities: list[str],
    cannot: list[str],
    extra_metadata: dict[str, object] | None = None,
) -> SubagentProfile:
    return SubagentProfile(
        subagent_id=subagent_id,
        name=name,
        role=role,  # type: ignore[arg-type]
        description=description,
        allowed_tool_categories=allowed,
        denied_tool_categories=list(dict.fromkeys([*denied, *EVIDENCE_CREATING_TOOL_CATEGORIES])),
        required_permissions=permissions,
        default_autonomy_level=autonomy,
        max_context_bytes=DEFAULT_CONTEXT_BYTES,
        can_delegate=can_delegate,
        can_request_approval=can_request_approval,
        can_execute_tools=can_execute_tools,
        can_write_artifacts=can_write_artifacts,
        guardrail_profile=guardrail_profile,
        metadata={
            "responsibilities": responsibilities,
            "cannot": cannot,
            "high_risk_tool_categories": HIGH_RISK_TOOL_CATEGORIES,
            "approval_required_tool_categories": HIGH_RISK_TOOL_CATEGORIES,
            "evidence_creating_actions_denied": True,
            **(extra_metadata or {}),
        },
    )


ProgramManagerSubagent = _profile(
    subagent_id="program-manager",
    name="ProgramManagerSubagent",
    role="program_manager",
    description="Decomposes user goals, coordinates work, tracks status, and manages consensus.",
    allowed=[
        "project",
        "jobs",
        "artifacts",
        "reports",
        "portfolio",
        "campaign",
        "evaluation_summary",
    ],
    denied=["scientific_evidence_write", "stage_gate_approval", "campaign_approval"],
    permissions=[
        "project:read",
        "job:read",
        "artifact:read",
        "report:write",
        "portfolio:read",
        "campaign:plan",
        "evaluation:read",
    ],
    can_delegate=True,
    guardrail_profile="program_coordination",
    responsibilities=[
        "decompose user goals",
        "delegate to other subagents",
        "track status",
        "coordinate consensus",
    ],
    cannot=["create scientific evidence", "approve gates"],
)

EvidenceReviewerSubagent = _profile(
    subagent_id="evidence-reviewer",
    name="EvidenceReviewerSubagent",
    role="evidence_reviewer",
    description="Reviews source evidence, checks provenance, and identifies missing evidence.",
    allowed=["ranking", "literature", "graph_query", "evidence_reports"],
    denied=["citation_write", "evidence_write", "assay_result_write"],
    permissions=["run:read", "literature:read", "graph:read", "evidence:read"],
    can_write_artifacts=False,
    guardrail_profile="evidence_grounding",
    responsibilities=[
        "review Open Targets, ChEMBL, PubMed, and OpenAlex evidence",
        "check provenance",
        "find missing evidence",
    ],
    cannot=["invent evidence", "invent citations"],
)

MoleculeDesignerSubagent = _profile(
    subagent_id="molecule-designer",
    name="MoleculeDesignerSubagent",
    role="molecule_designer",
    description="Plans generation/design loops and reviews generated molecule hypotheses.",
    allowed=["generation", "design", "developability", "oracle_scoring"],
    denied=["activity_claim", "out_of_pipeline_molecule_creation", "evidence_write"],
    permissions=["generation:run", "design:run", "developability:read", "oracle:score"],
    autonomy="execute_safe_tools",
    guardrail_profile="generated_molecule_hypothesis",
    responsibilities=[
        "plan generation and design loops",
        "use generation and design tools",
        "review generated molecule hypotheses",
    ],
    cannot=["claim activity", "create molecules outside generation pipeline"],
    extra_metadata={
        "generated_molecule_boundaries": [
            "generated molecules are computational hypotheses only",
            "activity claims require exact imported evidence",
            "molecules must originate from approved generation/design tools",
        ],
    },
)

BiologicsEngineerSubagent = _profile(
    subagent_id="biologics-engineer",
    name="BiologicsEngineerSubagent",
    role="biologics_engineer",
    description=(
        "Reviews antibody and biologic candidates, plans antibody-specific analysis, "
        "and critiques generated antibody hypotheses."
    ),
    allowed=[
        "biologics_retrieval",
        "antibody_validation",
        "antibody_developability",
        "antibody_novelty",
        "biologics_reports",
        "review",
        "portfolio",
        "campaign",
    ],
    denied=[
        "sequence_fabrication",
        "epitope_fabrication",
        "binding_claim",
        "assay_result_fabrication",
        "lab_protocol_write",
        "expression_purification_protocol",
        "dosing_guidance",
        "generated_antibody_advancement_approval",
    ],
    permissions=[
        "biologics:read",
        "biologics:analyze",
        "review:write",
        "portfolio:read",
        "campaign:plan",
    ],
    autonomy="execute_safe_tools",
    guardrail_profile="biologics_engineering",
    responsibilities=[
        "review antibody and biologic candidates",
        "plan antibody-specific analysis",
        "use governed biologics tools",
        "critique generated antibody hypotheses",
        "check sequence, developability, and novelty summaries",
    ],
    cannot=[
        "invent sequences",
        "invent epitopes",
        "invent binding claims",
        "invent assay results",
        "provide expression/purification/lab protocols",
        "provide dosing",
        "approve generated antibody advancement",
    ],
    extra_metadata={
        "codex_tasks": [
            "summarize_biologic_candidate",
            "explain_antibody_liabilities",
            "draft_biologics_review_questions",
            "summarize_antigen_context",
            "draft_biologics_campaign_summary",
        ],
        "generated_antibody_boundaries": [
            "generated antibodies are computational hypotheses only",
            "direct evidence requires exact imported experimental results",
            "sequence similarity or model output cannot establish binding",
            "generated antibodies require expert review before advancement",
        ],
    },
)

DevelopabilitySafetySubagent = _profile(
    subagent_id="developability-safety",
    name="DevelopabilitySafetySubagent",
    role="developability_safety",
    description="Reviews alerts, ADMET heuristics, safety warnings, and developability.",
    allowed=["developability", "safety_reports", "model_prediction_summaries"],
    denied=["clinical_safety_claim", "clinical_unsafe_claim"],
    permissions=["developability:read", "safety:read", "model:read"],
    guardrail_profile="developability_safety",
    responsibilities=[
        "review alerts",
        "review ADMET heuristics",
        "review safety warnings",
        "review developability",
    ],
    cannot=["claim safe or unsafe as a clinical conclusion"],
)

ExperimentAnalystSubagent = _profile(
    subagent_id="experiment-analyst",
    name="ExperimentAnalystSubagent",
    role="experiment_analyst",
    description="Imports and summarizes assay results, links outcomes, and flags QC issues.",
    allowed=["experiments", "active_learning", "result_summaries"],
    denied=["assay_result_fabrication", "failed_qc_support"],
    permissions=["experiment:write", "experiment:read", "active_learning:run"],
    guardrail_profile="experimental_feedback",
    responsibilities=[
        "import and summarize assay results",
        "link outcomes",
        "identify failed QC and contradictions",
    ],
    cannot=["fabricate results", "interpret failed QC as support"],
)

PredictiveModelerSubagent = _profile(
    subagent_id="predictive-modeler",
    name="PredictiveModelerSubagent",
    role="predictive_modeler",
    description="Builds/evaluates surrogates and reviews calibration/applicability domain.",
    allowed=["model", "evaluation"],
    denied=["prediction_to_evidence", "model_metric_fabrication"],
    permissions=["model:run", "evaluation:run"],
    guardrail_profile="predictive_modeling",
    responsibilities=[
        "build and evaluate surrogate models",
        "review calibration",
        "review applicability domain",
    ],
    cannot=["turn predictions into evidence"],
)

StructureReviewerSubagent = _profile(
    subagent_id="structure-reviewer",
    name="StructureReviewerSubagent",
    role="structure_reviewer",
    description="Reviews structure selection, docking, pose QC, and interaction profiles.",
    allowed=["structure", "docking", "pose_qc", "reports"],
    denied=["binding_claim", "docking_score_as_evidence"],
    permissions=["structure:read", "structure:run", "report:write"],
    guardrail_profile="structure_workflow",
    responsibilities=[
        "review structure selection",
        "review docking",
        "review pose QC",
        "review interaction profiles",
    ],
    cannot=["claim docking proves binding"],
)

GraphReasonerSubagent = _profile(
    subagent_id="graph-reasoner",
    name="GraphReasonerSubagent",
    role="graph_reasoner",
    description=(
        "Queries graph artifacts and extracts mechanisms, contradictions, and stale decisions."
    ),
    allowed=["graph", "mechanism", "contradiction"],
    denied=["unvalidated_graph_fact_write", "graph_truth_claim"],
    permissions=["graph:read", "mechanism:read", "contradiction:read"],
    guardrail_profile="graph_reasoning",
    responsibilities=[
        "query graph",
        "extract mechanisms",
        "extract contradictions",
        "extract stale decisions",
    ],
    cannot=["create graph facts without builder/validator"],
)

HypothesisPlannerSubagent = _profile(
    subagent_id="hypothesis-planner",
    name="HypothesisPlannerSubagent",
    role="hypothesis_planner",
    description="Generates and ranks graph-backed hypotheses and research questions.",
    allowed=["hypothesis", "graph"],
    denied=["protocol_write", "experimental_procedure_write"],
    permissions=["hypotheses:generate", "hypotheses:rank", "graph:read"],
    guardrail_profile="hypothesis_planning",
    responsibilities=[
        "generate graph-backed hypotheses",
        "rank graph-backed hypotheses",
        "create research questions",
    ],
    cannot=["write protocols"],
)

PortfolioStrategistSubagent = _profile(
    subagent_id="portfolio-strategist",
    name="PortfolioStrategistSubagent",
    role="portfolio_strategist",
    description="Optimizes portfolios and scenarios without approving gates.",
    allowed=["portfolio", "evaluation_summary"],
    denied=["stage_gate_approval", "campaign_approval"],
    permissions=["portfolio:run", "evaluation:read"],
    guardrail_profile="portfolio_strategy",
    responsibilities=["optimize portfolio", "run scenarios"],
    cannot=["approve stage gates"],
)

CampaignPlannerSubagent = _profile(
    subagent_id="campaign-planner",
    name="CampaignPlannerSubagent",
    role="campaign_planner",
    description="Builds high-level campaign plans and replan triggers.",
    allowed=["campaign", "portfolio", "hypothesis", "evaluation"],
    denied=["lab_protocol_write", "synthesis_instruction_write"],
    permissions=["campaign:plan", "portfolio:read", "hypotheses:read", "evaluation:read"],
    guardrail_profile="campaign_planning",
    responsibilities=["build high-level campaign plans", "build replan triggers"],
    cannot=["create lab protocols"],
)

IntegrationOperatorSubagent = _profile(
    subagent_id="integration-operator",
    name="IntegrationOperatorSubagent",
    role="integration_operator",
    description="Runs dry-run syncs, mapping reviews, and connector health checks.",
    allowed=["integration", "tool_marketplace"],
    denied=["unapproved_external_write", "secret_access"],
    permissions=["integration:read", "integration:write", "tool:read"],
    guardrail_profile="integration_operations",
    responsibilities=[
        "run dry-run syncs",
        "review mappings",
        "run connector health checks",
    ],
    cannot=["external write without approval"],
)

EvaluationValidatorSubagent = _profile(
    subagent_id="evaluation-validator",
    name="EvaluationValidatorSubagent",
    role="evaluation_validator",
    description="Runs benchmark, evaluation, guardrail, reproducibility, and release checks.",
    allowed=["evaluation", "validation", "release_checks"],
    denied=["metric_fabrication", "benchmark_result_fabrication"],
    permissions=["evaluation:run", "validation:run", "admin:release_check"],
    guardrail_profile="evaluation_validation",
    responsibilities=[
        "run benchmark checks",
        "run evaluation checks",
        "run guardrail checks",
        "run reproducibility checks",
    ],
    cannot=["invent metrics"],
)

GuardrailSentinelSubagent = _profile(
    subagent_id="guardrail-sentinel",
    name="GuardrailSentinelSubagent",
    role="guardrail_sentinel",
    description="Critiques outputs for safety and scientific guardrails.",
    allowed=["guardrail_benchmark", "validation"],
    denied=["scientific_output_mutation", "score_mutation", "evidence_write"],
    permissions=["evaluation:run", "validation:run"],
    can_write_artifacts=False,
    guardrail_profile="scientific_safety",
    responsibilities=["critique outputs for safety", "critique outputs for scientific guardrails"],
    cannot=["change scientific outputs directly"],
)

PlatformOperatorSubagent = _profile(
    subagent_id="platform-operator",
    name="PlatformOperatorSubagent",
    role="platform_operator",
    description="Runs readiness, support bundle, job, worker, health, and performance operations.",
    allowed=["admin", "support", "ops"],
    denied=["secret_access", "rbac_bypass", "credential_read"],
    permissions=["admin:readiness", "support:bundle", "ops:read"],
    guardrail_profile="platform_operations",
    responsibilities=[
        "run readiness checks",
        "generate support bundles",
        "inspect jobs and workers",
        "inspect health and performance",
    ],
    cannot=["access secrets or bypass RBAC"],
)


__all__ = [
    "CampaignPlannerSubagent",
    "BiologicsEngineerSubagent",
    "DevelopabilitySafetySubagent",
    "EVIDENCE_CREATING_TOOL_CATEGORIES",
    "EvaluationValidatorSubagent",
    "EvidenceReviewerSubagent",
    "ExperimentAnalystSubagent",
    "GraphReasonerSubagent",
    "GuardrailSentinelSubagent",
    "HIGH_RISK_TOOL_CATEGORIES",
    "HypothesisPlannerSubagent",
    "IntegrationOperatorSubagent",
    "MoleculeDesignerSubagent",
    "PlatformOperatorSubagent",
    "PortfolioStrategistSubagent",
    "PredictiveModelerSubagent",
    "ProgramManagerSubagent",
    "StructureReviewerSubagent",
    "SubagentRegistry",
    "builtin_subagent_profiles",
]
