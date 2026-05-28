from __future__ import annotations

from molecule_ranker.validation.schemas import GoldenWorkflow

COMMON_FORBIDDEN_OUTPUTS = [
    "cures",
    "treats",
    "prevents",
    "safe and efficacious",
    "is a validated active",
    "known active",
    "dosage",
    "synthesis route",
    "synthesis instructions",
    "lab protocol",
    "patient treatment",
    "fake citation",
    "fabricated evidence",
]


GOLDEN_WORKFLOWS: tuple[GoldenWorkflow, ...] = (
    GoldenWorkflow(
        workflow_id="existing_molecule_ranking",
        name="Existing Molecule Ranking",
        description=(
            "Disease to targets to existing candidates to literature to developability "
            "to source-backed report."
        ),
        inputs={
            "disease_name": "Synthetic Neuro Example",
            "mode": "deterministic_test",
            "external_services": "mocked",
        },
        expected_artifacts=[
            "disease.json",
            "targets.json",
            "candidates.json",
            "literature.json",
            "developability.json",
            "report.md",
            "trace.json",
        ],
        required_checks=[
            "all expected artifacts exist",
            "literature records are source-labeled synthetic validation records",
            "developability output is report-only and non-clinical",
            "report contains research-use disclaimer",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "candidate ranking completes with synthetic source provenance",
            "report separates evidence from limitations",
            "no medical, dosing, synthesis, or validated-active claims are emitted",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
    GoldenWorkflow(
        workflow_id="generation_workflow",
        name="Generation Workflow",
        description=(
            "Disease to evidence-backed seeds to generated hypotheses to developability "
            "to generated report."
        ),
        inputs={
            "disease_name": "Synthetic Neuro Example",
            "seed_source": "synthetic evidence-backed existing candidate",
            "generation_enabled": True,
            "external_services": "mocked",
        },
        expected_artifacts=[
            "seed_molecules.json",
            "generated_candidates.json",
            "generation_trace.json",
            "developability_assessments.json",
            "generated_report.md",
            "report.md",
        ],
        required_checks=[
            "seed molecules include source provenance",
            "generated molecules are labeled computational hypotheses",
            "generated molecules are ranked separately from existing candidates",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "generated hypotheses are created from synthetic seeds",
            "generated report preserves non-validated-hypothesis labeling",
            "no generated molecule is presented as experimentally validated",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
    GoldenWorkflow(
        workflow_id="review_workflow",
        name="Review Workflow",
        description=(
            "Run artifacts to review workspace to decisions/comments to dossier to "
            "validation handoff."
        ),
        inputs={"run_artifacts": "synthetic_run", "reviewer": "synthetic-reviewer"},
        expected_artifacts=[
            "review_queue.json",
            "review_workspace.json",
            "review_decisions.json",
            "review_comments.json",
            "candidate_dossier.md",
            "validation_handoff.json",
        ],
        required_checks=[
            "review decisions remain separate from biomedical evidence",
            "dossier contains source provenance and limitations",
            "handoff omits lab protocol content",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "review workspace is created from run artifacts",
            "review comments and handoff are artifact-grounded",
            "review output does not create evidence or assay results",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
    GoldenWorkflow(
        workflow_id="experimental_feedback_workflow",
        name="Experimental Feedback Workflow",
        description=(
            "Run artifacts to assay CSV import to result linking to score recalibration "
            "to active-learning batch."
        ),
        inputs={"assay_csv": "synthetic_assay_results.csv", "external_services": "mocked"},
        expected_artifacts=[
            "assay_results.csv",
            "experimental_results.json",
            "import_report.json",
            "experimental_evidence.json",
            "linked_results.json",
            "recalibrated_scores.json",
            "active_learning_batch.json",
        ],
        required_checks=[
            "assay CSV is synthetic and user-supplied",
            "results link only to exact synthetic candidate identifiers",
            "failed or inconclusive data are not treated as support",
            "active-learning output preserves validation disclaimers",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "assay CSV imports deterministically",
            "linked results produce bounded score recalibration metadata",
            "active-learning batch is a review queue, not a protocol",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
    GoldenWorkflow(
        workflow_id="codex_backbone_workflow",
        name="Codex Backbone Workflow",
        description=(
            "Run artifacts to Codex summary to Codex candidate explanation to guardrail "
            "verification."
        ),
        inputs={"codex_provider": "mocked", "run_artifacts": "synthetic_run"},
        expected_artifacts=[
            "codex_backbone.json",
            "codex_summary.json",
            "candidate_explanation.json",
            "guardrail_report.json",
        ],
        required_checks=[
            "Codex output is stored as assistant artifact",
            "Codex output does not become biomedical evidence",
            "guardrail verification passes",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "mocked Codex summary is artifact-grounded",
            "candidate explanation includes limitations",
            "guardrail report has no violations",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
    GoldenWorkflow(
        workflow_id="hosted_platform_workflow",
        name="Hosted Platform Workflow",
        description=(
            "User login to project creation to job run to dashboard view to project export."
        ),
        inputs={"user": "synthetic-admin@example.test", "external_services": "mocked"},
        expected_artifacts=[
            "auth_session.json",
            "project.json",
            "job_record.json",
            "dashboard_snapshot.html",
            "project_export_manifest.json",
            "project_export.zip",
        ],
        required_checks=[
            "auth session is synthetic and contains no token secret",
            "job record is deterministic",
            "dashboard includes research-use disclaimer",
            "export manifest includes hashes and excludes secrets",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "hosted workflow completes without live services",
            "export manifest is reproducible",
            "no secret-like values are emitted",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
    GoldenWorkflow(
        workflow_id="integration_sync_workflow",
        name="Integration Sync Workflow",
        description="External system config to dry-run sync to mapping review to artifact export.",
        inputs={"external_system": "synthetic-generic-rest", "mode": "dry_run"},
        expected_artifacts=[
            "external_system_config.json",
            "dry_run_sync_report.json",
            "mapping_review.json",
            "artifact_export_manifest.json",
            "integration_sync.json",
        ],
        required_checks=[
            "connector mode is dry-run",
            "mapping review is deterministic",
            "Codex suggestions are not accepted without deterministic confirmation",
            "artifact export excludes secrets",
            "forbidden output scan passes",
        ],
        forbidden_outputs=COMMON_FORBIDDEN_OUTPUTS,
        success_criteria=[
            "dry-run sync completes with synthetic records",
            "mapping review records confirmation evidence",
            "artifact export is read-only",
        ],
        metadata={"release": "1.0.0", "live_validation": "opt_in_only"},
    ),
)


def list_golden_workflows() -> list[GoldenWorkflow]:
    return list(GOLDEN_WORKFLOWS)


def get_golden_workflow(workflow_id: str) -> GoldenWorkflow:
    for workflow in GOLDEN_WORKFLOWS:
        if workflow.workflow_id == workflow_id:
            return workflow
    raise KeyError(f"Unknown golden workflow: {workflow_id}")
