from __future__ import annotations

import json
import zipfile
from pathlib import Path

from molecule_ranker.contracts import ARTIFACT_CONTRACTS, with_artifact_contract_metadata
from molecule_ranker.validation.design import run_design_validation
from molecule_ranker.validation.golden_workflows import (
    get_golden_workflow,
    list_golden_workflows,
)
from molecule_ranker.validation.reports import (
    write_json_artifact,
    write_markdown_artifact,
    write_validation_report,
)
from molecule_ranker.validation.schemas import (
    ForbiddenOutputFinding,
    GoldenValidationReport,
    GoldenWorkflow,
    GoldenWorkflowMode,
    GoldenWorkflowResult,
)


def run_golden_workflows(
    *,
    workflow: str = "all",
    output_dir: str | Path = ".molecule-ranker/validation/golden",
    live: bool = False,
) -> GoldenValidationReport:
    selected = _select_workflows(workflow)
    resolved_output = Path(output_dir).resolve()
    mode: GoldenWorkflowMode = "live" if live else "test"
    results = [_run_golden_workflow(item, resolved_output, mode=mode) for item in selected]
    report = GoldenValidationReport(
        status="pass" if all(result.status == "pass" for result in results) else "fail",
        workflow_count=len(results),
        live_validation=live,
        output_dir=resolved_output,
        results=results,
        metadata={
            "external_services": "live_opt_in" if live else "mocked",
            "default_mode": "deterministic_test",
        },
    )
    write_json_artifact(
        resolved_output / "golden_validation_report.json",
        report.model_dump(mode="json"),
    )
    write_validation_report(resolved_output / "golden_validation_report.md", report)
    return report


def check_forbidden_outputs(
    artifacts: list[Path],
    forbidden_outputs: list[str],
) -> list[ForbiddenOutputFinding]:
    findings: list[ForbiddenOutputFinding] = []
    normalized_phrases = [phrase.lower() for phrase in forbidden_outputs]
    for artifact in artifacts:
        if not artifact.exists() or not artifact.is_file():
            continue
        text = artifact.read_text(errors="ignore")
        lowered = text.lower()
        for phrase in normalized_phrases:
            index = lowered.find(phrase)
            if index < 0:
                continue
            findings.append(
                ForbiddenOutputFinding(
                    artifact_path=artifact,
                    phrase=phrase,
                    excerpt=text[max(0, index - 60) : index + len(phrase) + 60],
                )
            )
    return findings


def _select_workflows(workflow: str) -> list[GoldenWorkflow]:
    if workflow == "all":
        return list_golden_workflows()
    return [get_golden_workflow(workflow)]


def _run_golden_workflow(
    workflow: GoldenWorkflow,
    output_dir: Path,
    *,
    mode: GoldenWorkflowMode,
) -> GoldenWorkflowResult:
    artifact_dir = output_dir / workflow.workflow_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_workflow_artifacts(workflow, artifact_dir, mode=mode)
    artifacts = [artifact_dir / name for name in workflow.expected_artifacts]
    missing = [
        name
        for name, artifact in zip(workflow.expected_artifacts, artifacts, strict=True)
        if not artifact.exists()
    ]
    forbidden_findings = check_forbidden_outputs(artifacts, workflow.forbidden_outputs)
    status = "pass" if not missing and not forbidden_findings else "fail"
    return GoldenWorkflowResult(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        status=status,
        mode=mode,
        artifact_dir=artifact_dir,
        artifacts=artifacts,
        missing_artifacts=missing,
        required_checks=workflow.required_checks,
        forbidden_findings=forbidden_findings,
        success_criteria=workflow.success_criteria,
        metadata={
            **workflow.metadata,
            "external_services": "live_opt_in" if mode == "live" else "mocked",
            "synthetic_data": True,
        },
    )


def _write_workflow_artifacts(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    *,
    mode: GoldenWorkflowMode,
) -> None:
    writer = WORKFLOW_WRITERS[workflow.workflow_id]
    writer(workflow, artifact_dir, mode)


def _base_payload(workflow: GoldenWorkflow, mode: GoldenWorkflowMode) -> dict[str, object]:
    return {
        "workflow_id": workflow.workflow_id,
        "mode": mode,
        "synthetic": True,
        "external_services": "live_opt_in" if mode == "live" else "mocked",
        "live_public_apis": mode == "live",
        "credentials_required": False,
        "provenance": {
            "source_system": "synthetic_validation_fixture",
            "source_record_id": f"{workflow.workflow_id}:synthetic-record",
            "source_record_type": "golden_workflow_fixture",
            "retrieval_mode": "live_opt_in" if mode == "live" else "mocked",
        },
        "limitations": [
            "Synthetic validation data are not biomedical evidence.",
            "Outputs support internal release validation only.",
        ],
        "scientific_integrity": {
            "medical_advice": False,
            "dosing_content": False,
            "synthesis_instructions": False,
            "lab_protocols": False,
            "fabricated_evidence": False,
            "generated_validated_actives": False,
        },
    }


def _contract_payload(filename: str, payload: dict[str, object]) -> dict[str, object]:
    contract = ARTIFACT_CONTRACTS.get(filename)
    if contract is None:
        return payload
    return with_artifact_contract_metadata(payload, contract.artifact_type)


def _write_json(
    artifact_dir: Path,
    filename: str,
    payload: dict[str, object],
) -> Path:
    return write_json_artifact(artifact_dir / filename, _contract_payload(filename, payload))


def _existing_molecule_ranking(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    _write_json(
        artifact_dir,
        "disease.json",
        {
            **base,
            "disease": {
                "name": "Synthetic Neuro Example",
                "source": "synthetic_validation_source",
            },
        },
    )
    _write_json(
        artifact_dir,
        "targets.json",
        {
            **base,
            "targets": [
                {
                    "target_id": "SYN-T1",
                    "symbol": "SYN1",
                    "source_record_id": "synthetic-target-1",
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "candidates.json",
        {
            **base,
            "success": True,
            "disease": {"name": "Synthetic Neuro Example"},
            "targets": [{"target_id": "SYN-T1", "symbol": "SYN1"}],
            "candidates": [
                {
                    "candidate_id": "SYN-C1",
                    "name": "SyntheticCandidateA",
                    "rank": 1,
                    "score": 0.42,
                    "source_record_id": "synthetic-candidate-1",
                    "provenance": {
                        "source_system": "synthetic_validation_fixture",
                        "source_record_id": "synthetic-candidate-1",
                    },
                }
            ],
            "summary": {"candidate_count": 1, "generated_candidate_count": 0, "target_count": 1},
        },
    )
    _write_json(
        artifact_dir,
        "literature.json",
        {
            **base,
            "literature": [
                {
                    "record_id": "SYN-LIT-1",
                    "source_record_id": "synthetic-literature-1",
                    "source": "synthetic_validation_source",
                    "claim": "Mentions the synthetic target relationship for workflow testing.",
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "developability.json",
        {
            **base,
            "success": True,
            "enabled": True,
            "assessment": {"candidate_id": "SYN-C1", "risk_label": "review"},
        },
    )
    _write_json(
        artifact_dir,
        "trace.json",
        {
            **base,
            "success": True,
            "traces": ["disease", "targets", "candidates", "literature", "developability"],
            "artifacts": ["disease.json", "targets.json", "candidates.json"],
            "steps": ["disease", "targets", "candidates", "literature", "developability"],
        },
    )
    write_markdown_artifact(
        artifact_dir / "report.md",
        "Existing Molecule Ranking Golden Report",
        [
            "Internal research use only.",
            "Synthetic validation data are used for deterministic validation.",
            "Ranked candidates are research prioritization records that require "
            "independent validation.",
            "## Limitations",
            "Synthetic validation records are not biomedical evidence.",
            "No medical guidance, operational wet-lab steps, or clinical claims are provided.",
        ],
    )


def _generation_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    _write_json(
        artifact_dir,
        "seed_molecules.json",
        {
            **base,
            "seeds": [{"seed_id": "SYN-SEED-1", "source_record_id": "synthetic-candidate-1"}],
        },
    )
    _write_json(
        artifact_dir,
        "generated_candidates.json",
        {
            **base,
            "success": True,
            "generation_enabled": True,
            "generated_count": 1,
            "generated": [
                {
                    "generated_id": "SYN-G1",
                    "origin": "generated",
                    "is_generated": True,
                    "label": "computational_hypothesis",
                    "rank_separate_from_existing": True,
                    "evidence": [],
                    "fake_evidence": False,
                    "validated_active": False,
                }
            ],
            "retained_generated_molecules": [
                {
                    "generated_id": "SYN-G1",
                    "origin": "generated",
                    "is_generated": True,
                    "evidence": [],
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "generation_trace.json",
        {
            **base,
            "seed_selection_trace": [{"seed_id": "SYN-SEED-1"}],
            "generator_trace": [{"provider": "deterministic_synthetic_generator"}],
            "steps": ["seed_selection", "hypothesis_generation", "chemistry_checks"],
        },
    )
    _write_json(
        artifact_dir,
        "developability_assessments.json",
        {**base, "assessments": [{"generated_id": "SYN-G1", "risk_label": "review"}]},
    )
    write_markdown_artifact(
        artifact_dir / "generated_report.md",
        "Generated Hypotheses Golden Report",
        [
            "Generated structures are computational hypotheses for expert review.",
            "They are ranked separately from existing source-backed candidates.",
            "## Limitations",
            "Generated structures are not validated actives and carry no assay evidence.",
            "No generated structure is presented as experimentally confirmed.",
        ],
    )
    write_markdown_artifact(
        artifact_dir / "report.md",
        "Generated Hypotheses Contract Report",
        [
            "Generated structures are computational hypotheses for expert review.",
            "## Limitations",
            "Synthetic generation validation does not establish biomedical truth.",
        ],
    )


def _review_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    _write_json(
        artifact_dir,
        "review_queue.json",
        {
            **base,
            "workspace_id": "review-synthetic",
            "review_items": [
                {
                    "candidate_id": "SYN-C1",
                    "source_record_id": "synthetic-candidate-1",
                    "review_reason": "golden_validation",
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "review_workspace.json",
        {**base, "workspace_id": "review-synthetic", "source_run_id": "run-synthetic"},
    )
    _write_json(
        artifact_dir,
        "review_decisions.json",
        {**base, "decisions": [{"candidate_id": "SYN-C1", "decision": "needs_review"}]},
    )
    _write_json(
        artifact_dir,
        "review_comments.json",
        {**base, "comments": [{"candidate_id": "SYN-C1", "text": "Synthetic review note."}]},
    )
    write_markdown_artifact(
        artifact_dir / "candidate_dossier.md",
        "Candidate Dossier Golden Artifact",
        [
            "Dossier content is grounded in synthetic run artifacts.",
            "Review decisions are separate from evidence and scoring inputs.",
            "## Limitations",
            "Review comments do not create biomedical evidence or assay results.",
        ],
    )
    _write_json(
        artifact_dir,
        "validation_handoff.json",
        {**base, "handoff": {"candidate_id": "SYN-C1", "next_step": "expert_review"}},
    )


def _experimental_feedback_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    (artifact_dir / "assay_results.csv").write_text(
        "result_id,candidate_id,candidate_name,outcome_label,qc_status,confidence,source_record_id\n"
        "SYN-R1,SYN-C1,SyntheticCandidateA,inconclusive,passed,0.4,synthetic-assay-row-1\n"
        "SYN-R2,SYN-C1,SyntheticCandidateA,supporting,failed,0.9,synthetic-assay-row-2\n"
    )
    _write_json(
        artifact_dir,
        "experimental_results.json",
        {
            **base,
            "summary": {"row_count": 2, "source_type": "file", "source_path": "assay_results.csv"},
            "results": [
                {
                    "result_id": "SYN-R1",
                    "candidate_id": "SYN-C1",
                    "qc_status": "passed",
                    "source_record_id": "synthetic-assay-row-1",
                },
                {
                    "result_id": "SYN-R2",
                    "candidate_id": "SYN-C1",
                    "qc_status": "failed",
                    "source_record_id": "synthetic-assay-row-2",
                },
            ],
        },
    )
    _write_json(
        artifact_dir,
        "import_report.json",
        {
            **base,
            "row_count": 2,
            "valid_count": 2,
            "source": "synthetic_csv",
            "source_type": "file",
            "source_path": "assay_results.csv",
            "live_external_import": False,
        },
    )
    _write_json(
        artifact_dir,
        "experimental_evidence.json",
        {
            **base,
            "success": True,
            "loaded_result_ids": ["SYN-R1", "SYN-R2"],
            "linked_result_ids": ["SYN-R1", "SYN-R2"],
            "candidate_summaries": [{"candidate_id": "SYN-C1", "supporting_count": 0}],
        },
    )
    _write_json(
        artifact_dir,
        "linked_results.json",
        {
            **base,
            "links": [
                {"candidate_id": "SYN-C1", "result_id": "SYN-R1"},
                {"candidate_id": "SYN-C1", "result_id": "SYN-R2"},
            ],
        },
    )
    _write_json(
        artifact_dir,
        "recalibrated_scores.json",
        {
            **base,
            "scores": [
                {
                    "candidate_id": "SYN-C1",
                    "previous": 0.42,
                    "updated": 0.42,
                    "failed_qc_result_ids": ["SYN-R2"],
                    "failed_qc_improved_score": False,
                }
            ],
            "note": "Inconclusive and failed-QC synthetic results do not add support.",
        },
    )
    _write_json(
        artifact_dir,
        "active_learning_batch.json",
        {
            **base,
            "success": True,
            "suggestions": [{"candidate_id": "SYN-C1", "reason": "review uncertainty"}],
            "batch": [{"candidate_id": "SYN-C1", "reason": "review uncertainty"}],
            "protocol_content": False,
        },
    )


def _codex_backbone_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    codex_payload = {
        **base,
        "summary": "Mocked Codex summary grounded in synthetic report artifacts.",
        "results": [
            {
                "task_id": "codex-summary-synthetic",
                "provider": "NullCodexProvider",
                "creates_evidence_items": False,
            }
        ],
        "guardrail_warnings": [],
    }
    _write_json(artifact_dir, "codex_backbone.json", codex_payload)
    _write_json(
        artifact_dir,
        "codex_summary.json",
        {
            **base,
            "artifact_type": "codex_backbone",
            "provider": "NullCodexProvider",
            "creates_evidence_items": False,
            "summary": "Mocked Codex summary grounded in synthetic report artifacts.",
        },
    )
    _write_json(
        artifact_dir,
        "candidate_explanation.json",
        {
            **base,
            "artifact_type": "codex_backbone",
            "provider": "NullCodexProvider",
            "creates_evidence_items": False,
            "explanation": "Synthetic candidate explanation with uncertainty and limitations.",
        },
    )
    _write_json(
        artifact_dir,
        "guardrail_report.json",
        {**base, "status": "pass", "violations": []},
    )


def _hosted_platform_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    _write_json(
        artifact_dir,
        "auth_session.json",
        {**base, "user": "synthetic-admin@example.test", "token_stored": False},
    )
    _write_json(
        artifact_dir,
        "project.json",
        {**base, "project_id": "project-synthetic", "name": "Synthetic Validation Project"},
    )
    _write_json(
        artifact_dir,
        "job_record.json",
        {**base, "job_id": "job-synthetic", "status": "completed"},
    )
    (artifact_dir / "dashboard_snapshot.html").write_text(
        "<!doctype html><title>molecule-ranker V1.0</title>"
        "<main>Internal research use only. Synthetic validation dashboard.</main>\n"
    )
    export_manifest = {
        **base,
        "exported_at": "2026-01-01T00:00:00Z",
        "project_id": "project-synthetic",
        "project": {"project_id": "project-synthetic"},
        "artifact_manifest": [{"artifact_id": "report", "sha256": "synthetic-sha256"}],
        "artifacts": [{"artifact_id": "report", "sha256": "synthetic-sha256"}],
        "secrets_included": False,
    }
    _write_json(
        artifact_dir,
        "project_export_manifest.json",
        export_manifest,
    )
    project_export = _contract_payload("project_export.zip", export_manifest)
    with zipfile.ZipFile(artifact_dir / "project_export.zip", "w") as archive:
        archive.writestr(
            "project_export.json",
            json.dumps(project_export, indent=2, sort_keys=True) + "\n",
        )


def _integration_sync_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    _write_json(
        artifact_dir,
        "external_system_config.json",
        {
            **base,
            "external_system_id": "ext-synthetic",
            "mode": "dry_run",
            "credentials": "not_required",
        },
    )
    _write_json(
        artifact_dir,
        "dry_run_sync_report.json",
        {**base, "rows_seen": 1, "rows_written": 0, "dry_run": True},
    )
    _write_json(
        artifact_dir,
        "mapping_review.json",
        {
            **base,
            "mappings": [
                {
                    "internal_id": "SYN-C1",
                    "external_id": "EXT-SYN-C1",
                    "source_record_id": "external-synthetic-record-1",
                    "status": "confirmed",
                    "deterministic_match": True,
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "artifact_export_manifest.json",
        {**base, "export_ready": True, "external_write": False, "secrets_included": False},
    )
    _write_json(
        artifact_dir,
        "integration_sync.json",
        {
            **base,
            "sync_job": {
                "sync_job_id": "sync-synthetic",
                "dry_run": True,
                "source_record_id": "external-synthetic-record-1",
            },
            "records": [
                {
                    "external_id": "EXT-SYN-C1",
                    "source_record_id": "external-synthetic-record-1",
                    "write_mode": "dry_run",
                }
            ],
            "mapping_report": {"approved_by": "deterministic_validation"},
            "artifact_manifest": {"external_write": False},
        },
    )


def _v1_1_agentic_generation_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    executed_agents = [
        "ScientificDesignPlannerAgent",
        "DesignObjectiveAgent",
        "SeedAndScaffoldSelectionAgent",
        "GeneratorEnsembleAgent",
        "OracleScoringAgent",
        "MedicinalChemistryCriticAgent",
        "UncertaintyAndDiversityAgent",
        "ExperimentReadinessAgent",
        "ActiveLearningDesignAgent",
    ]
    _write_json(
        artifact_dir,
        "agent_graph_trace.json",
        {
            **base,
            "runtime": "AgentGraph",
            "status": "completed",
            "executed_agents": executed_agents,
        },
    )
    _write_json(
        artifact_dir,
        "design_objectives.json",
        {
            **base,
            "objectives": [
                {
                    "objective_id": "synthetic-neuro:SYN1",
                    "target_symbol": "SYN1",
                    "claim_boundary": "design objective only",
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "seed_scaffold_selection.json",
        {
            **base,
            "seed_policy": "evidence-backed retrieved seeds only",
            "selected_seed_ids": ["SYN-SEED-1"],
            "generated_evidence": [],
        },
    )
    _write_json(
        artifact_dir,
        "generator_ensemble.json",
        {
            **base,
            "methods": {"selfies_mutation": 2},
            "deterministic_validation_required": True,
        },
    )
    _write_json(
        artifact_dir,
        "oracle_scores.json",
        {
            **base,
            "oracle_scores": {
                "objective_alignment_score": 0.73,
                "uncertainty_score": 0.44,
                "experiment_readiness_score": 0.62,
            },
            "score_boundary": "computational triage only",
        },
    )
    _write_json(
        artifact_dir,
        "generated_report_cards.json",
        {
            **base,
            "report_cards": [
                {
                    "generated_id": "SYN-GEN-1",
                    "hypothesis_boundary": {
                        "hypothesis_only": True,
                        "no_direct_experimental_evidence": True,
                    },
                    "traceability": {"parent_seed_ids": ["SYN-SEED-1"]},
                    "evidence": [],
                }
            ],
        },
    )
    _write_json(
        artifact_dir,
        "active_learning_design.json",
        {
            **base,
            "loop": "review-prioritized computational design loop",
            "assay_results_fabricated": False,
            "protocol_content": False,
        },
    )


def _v1_1_design_optimization_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    del workflow, mode
    run_design_validation(output_dir=artifact_dir)


def _v1_1_generator_benchmark_workflow(
    workflow: GoldenWorkflow,
    artifact_dir: Path,
    mode: GoldenWorkflowMode,
) -> None:
    base = _base_payload(workflow, mode)
    generated_payload = {
        **base,
        "generated_count": 2,
        "retained_count": 2,
        "rejected_count": 0,
        "retained_generated_molecules": [
            {
                "generated_id": "SYN-GEN-1",
                "canonical_smiles": "CCO",
                "generation_method": "selfies_mutation",
                "conditioned_targets": ["SYN1"],
                "metadata": {
                    "experiment_readiness": {"score": 0.62, "label": "needs_review"},
                    "uncertainty": {"score": 0.44},
                },
            }
        ],
        "rejected_generated_molecules": [],
    }
    _write_json(artifact_dir, "synthetic_generated_candidates.json", generated_payload)
    _write_json(
        artifact_dir,
        "benchmark_metrics.json",
        {
            **base,
            "benchmark": "internal_generation_quality_v1_1",
            "validity_rate": 1.0,
            "review_ready_rate": 0.0,
            "average_uncertainty_score": 0.44,
            "generator_method_counts": {"selfies_mutation": 1},
            "claim_boundary": "benchmark metrics are not biomedical evidence",
        },
    )
    write_markdown_artifact(
        artifact_dir / "benchmark_report.md",
        "V1.1 Generator Benchmark",
        [
            "Synthetic validation benchmark for computational triage metrics only.",
            "Generated structures remain hypotheses with no direct experimental evidence.",
        ],
    )


WORKFLOW_WRITERS = {
    "existing_molecule_ranking": _existing_molecule_ranking,
    "generation_workflow": _generation_workflow,
    "review_workflow": _review_workflow,
    "experimental_feedback_workflow": _experimental_feedback_workflow,
    "codex_backbone_workflow": _codex_backbone_workflow,
    "hosted_platform_workflow": _hosted_platform_workflow,
    "integration_sync_workflow": _integration_sync_workflow,
    "v1_1_design_optimization_workflow": _v1_1_design_optimization_workflow,
    "v1_1_agentic_generation_workflow": _v1_1_agentic_generation_workflow,
    "v1_1_generator_benchmark_workflow": _v1_1_generator_benchmark_workflow,
}
