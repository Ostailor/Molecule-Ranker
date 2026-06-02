from __future__ import annotations

from pathlib import Path
from typing import Any


def render_release_notes(manifest: dict[str, Any]) -> str:
    version = manifest.get("version", "unknown")
    git_commit = manifest.get("git_commit", "unknown")
    build_timestamp = manifest.get("build_timestamp", "unknown")
    limitations = manifest.get("known_limitations", [])
    return "\n".join(
        [
            f"# molecule-ranker {version} Release Notes",
            "",
            "## Release Scope",
            "",
            "V1.9 upgrades the V1.8 validated internal research platform with "
            "enterprise/internal pilot readiness: usability polish, performance "
            "optimization, reliability hardening, operational readiness, pilot "
            "onboarding, admin/support workflows, better error messages, robust "
            "job retry/resume/cancel expectations, dashboard workflow improvements, "
            "dataset/artifact migration safety, deployment diagnostics, monitoring "
            "and alerting guidance, pilot feedback capture, support bundle "
            "generation, and pre-V2.0 readiness validation. V1.9 does not add "
            "major new science capabilities and does not expand molecule generation, "
            "docking, ADMET, external connectors, or predictive modeling except "
            "for stability and usability improvements. The validated internal "
            "research platform MVP boundary remains intact: "
            "campaign plans, hypotheses, research questions, graph paths, portfolio "
            "recommendations, structure workflows, docking scores, model predictions, "
            "and generated molecules are planning or prioritization signals, not "
            "biomedical claims.",
            "",
            "## Included",
            "",
            "- End-to-end golden workflows in deterministic mocked validation mode.",
            "- Versioned artifact and API contracts for V1.0 platform outputs.",
            "- AgentGraph runtime for scientific design planning and traceability.",
            "- Formal model plugin interface for local and future external providers.",
            "- Assay-specific surrogate model cards, manifests, metrics, and prediction "
            "artifacts kept separate from evidence and assay results.",
            "- Conservative target structure selection that prefers suitable experimental "
            "structures over predicted models.",
            "- Receptor, ligand 3D, pose QC, consensus rescoring, and interaction-profile "
            "artifact schemas with provenance and guardrails.",
            "- Structure-based report cards that keep docking and pose outputs separate "
            "from experimental evidence and activity claims.",
            "- Program, portfolio, candidate, objective, constraint, optimization-run, "
            "selection, scenario, stage-gate, budget, sensitivity, and decision-memo "
            "schemas for V1.4 portfolio analytics.",
            "- KnowledgeGraph, GraphEntity, and GraphRelation schemas for V1.5 "
            "cross-program memory with explicit provenance and hypothesis boundaries.",
            "- Ontology and identifier mapping for disease, target, molecule, mechanism, "
            "scaffold, assay-result, risk, literature-claim, and expert-decision nodes.",
            "- Deterministic graph building from existing ranking, assay, review, "
            "developability, portfolio, and generated-molecule artifacts.",
            "- Cross-program reasoning queries for recurring mechanisms, target outcomes, "
            "scaffold/family patterns, contradictions, staleness, repeated blockers, "
            "review-outcome correlations, novelty checks, and prior-knowledge reuse.",
            "- Graph dashboard and Codex graph assistant with guardrails that prevent "
            "invented nodes, edges, mechanisms, citations, evidence, or assay results.",
            "- Hypothesis schemas, deterministic hypothesis generation, evidence-gap "
            "analysis, falsification criteria, ranking, lifecycle tracking, review "
            "queues, dashboard rendering, and a guarded Codex hypothesis assistant.",
            "- Campaign schemas, deterministic campaign planning, budget-fit calculation, "
            "review-gated work packages, slot allocation, replan triggers, audit trails, "
            "campaign memos, and campaign dashboard rendering for V1.7.",
            "- BenchmarkSuite, BenchmarkTask, BenchmarkDataset, BenchmarkSplit, "
            "FrozenPredictionSet, ProspectiveValidationRun, EvaluationMetric, "
            "EvaluationReport, DecisionQualityReport, and ReproducibilityManifest "
            "schemas for V1.8.",
            "- V1.9 pilot readiness reports covering usability, performance, "
            "reliability, operations, onboarding, admin/support, error messaging, "
            "job control, dashboard workflows, migration safety, deployment "
            "diagnostics, monitoring, pilot feedback, support bundles, and pre-V2 "
            "validation.",
            "- Support bundle manifests that list safe diagnostics without file "
            "contents, environment variables, cache files, API keys, service "
            "tokens, credentials, or plaintext secrets.",
            "- Deterministic evaluation metrics for ranking, generated experiment-worthiness, "
            "surrogate calibration, portfolio baselines, campaign learning, hypothesis "
            "lifecycle, integration provenance, guardrail failures, and reproducibility.",
            "- Deterministic multi-objective portfolio selection with diversity, "
            "learning-value, uncertainty, budget, and correlated-risk constraints.",
            "- Expert-review, assay-triage, learning-batch, deprioritization, "
            "human-approval, and scenario-robustness queues computed without Codex.",
            "- Hosted structure job types with explicit docking limitation acknowledgements.",
            "- Generated report cards with objective alignment, uncertainty, diversity, "
            "critique, readiness, and active-learning triage fields.",
            "- Generator benchmark metrics for readiness, uncertainty, and generator coverage.",
            "- Security, guardrail, provenance, integration, deployment, and release checks.",
            "- Operator, admin, and user documentation with backup/restore runbooks.",
            "- Synthetic demo project artifacts clearly labeled as non-evidence examples.",
            "",
            "## Safety And Integrity",
            "",
            "- research use only",
            "- no medical advice",
            "- no clinical claims",
            "- no lab protocols",
            "- no synthesis instructions",
            "- no dosing",
            "- generated molecules require validation",
            "- docking scores are not proof of binding",
            "- poses are not experimental evidence",
            "- structure-based scores are not activity evidence",
            "- portfolio recommendations are prioritization aids only",
            "- knowledge graph paths are memory and reasoning aids only",
            "- graph inference must not create EvidenceItem records or assay results",
            "- graph-inferred relationships are hypotheses unless source-backed",
            "- automated hypotheses are planning artifacts, not evidence",
            "- research questions and validation plans are not experimental procedures",
            "- campaign plans are research-management artifacts, not lab protocols",
            "- campaign priorities, budget fit, dependencies, and replan triggers are "
            "computed deterministically",
            "- benchmark results are evaluation artifacts, not biomedical evidence",
            "- prospective validation analytics are not clinical validation",
            "- Codex must not invent benchmark results, labels, metrics, or conclusions",
            "- Codex must not create portfolio selections, scores, evidence, assay "
            "results, citations, molecules, graph nodes, graph edges, mechanisms, or "
            "unvalidated hypotheses",
            "- Codex must not create campaign metrics, costs, outcomes, or advancement "
            "decisions",
            "- predicted structures are lower-confidence than suitable experimental structures",
            "- Codex outputs are assistant artifacts, not biomedical evidence",
            "- Codex output must not become evidence, assay results, molecules, "
            "scores, benchmark results, or decisions",
            "- V1.9 pilot feedback is operational feedback, not biomedical evidence",
            "- V1.9 support bundles are diagnostics manifests, not evidence packages",
            "",
            "## Contracts",
            "",
            f"- Artifact contract version: {manifest.get('artifact_contract_version', 'unknown')}",
            f"- API contract version: {manifest.get('api_contract_version', 'unknown')}",
            "",
            "## Build",
            "",
            f"- Git commit: {git_commit}",
            f"- Build timestamp: {build_timestamp}",
            f"- Dependency lock hash: {manifest.get('dependency_lock_hash', 'unknown')}",
            "",
            "## Known Limitations",
            "",
            *[f"- {item}" for item in limitations],
            "",
        ]
    )


def write_release_notes(notes: str, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(notes)
    return target
