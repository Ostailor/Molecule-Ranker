from __future__ import annotations

from pathlib import Path
from typing import Any

USABILITY_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "first_run_setup_clarity",
        "First-run setup clarity",
        ("molecule_ranker/web/templates/project_list.html",),
    ),
    (
        "dashboard_empty_states",
        "Dashboard empty states",
        (
            "molecule_ranker/web/templates/project_list.html",
            "molecule_ranker/web/templates/project_detail.html",
        ),
    ),
    (
        "job_failure_explanations",
        "Job failure explanations",
        ("molecule_ranker/pilot/usability.py", "molecule_ranker/web/templates/project_detail.html"),
    ),
    (
        "artifact_missing_explanations",
        "Artifact missing explanations",
        ("molecule_ranker/pilot/usability.py", "molecule_ranker/web/templates/project_detail.html"),
    ),
    (
        "generated_molecule_warnings_visibility",
        "Generated molecule warnings visibility",
        ("molecule_ranker/web/templates/project_detail.html",),
    ),
    (
        "codex_output_labeling",
        "Codex output labeling",
        ("molecule_ranker/web/templates/project_detail.html", "docs/user/codex_assistant.md"),
    ),
    (
        "model_prediction_labeling",
        "Model prediction labeling",
        ("molecule_ranker/web/templates/project_detail.html", "docs/user/overview.md"),
    ),
    (
        "benchmark_evaluation_labeling",
        "Benchmark/evaluation labeling",
        ("molecule_ranker/web/templates/project_detail.html", "docs/user/evaluation_benchmarks.md"),
    ),
    (
        "review_workflow_discoverability",
        "Review workflow discoverability",
        ("molecule_ranker/web/templates/project_detail.html", "docs/user/review_workflow.md"),
    ),
    (
        "campaign_workflow_discoverability",
        "Campaign workflow discoverability",
        ("molecule_ranker/web/templates/project_detail.html", "docs/user/campaigns.md"),
    ),
    (
        "integration_dry_run_write_mode_clarity",
        "Integration dry-run/write-mode clarity",
        ("molecule_ranker/web/templates/project_list.html", "docs/user/integrations.md"),
    ),
)

ERROR_EXPLANATIONS: dict[str, dict[str, str]] = {
    "artifact-not-found": {
        "title": "Artifact not found",
        "meaning": "The requested artifact was not found in the selected project registry.",
        "remediation": (
            "Check that the artifact ID belongs to the selected project, refresh the project "
            "artifact list, then retry the download."
        ),
    },
    "job-failed": {
        "title": "Job failed",
        "meaning": "A queued job stopped before completion.",
        "remediation": (
            "Open the job detail, review the redacted error summary, verify inputs and "
            "permissions, then retry after correcting the cause."
        ),
    },
    "worker-unhealthy": {
        "title": "Worker unhealthy",
        "meaning": "The background worker queue is not accepting or completing jobs.",
        "remediation": (
            "Check worker process status, database connectivity, queue permissions, and "
            "recent audit events."
        ),
    },
}


def run_usability_checks(root_dir: str | Path = ".") -> dict[str, Any]:
    root = Path(root_dir).resolve()
    checks = []
    for check_id, title, evidence in USABILITY_CHECKS:
        missing = [path for path in evidence if not (root / path).exists()]
        checks.append(
            {
                "check_id": check_id,
                "title": title,
                "status": "pass" if not missing else "fail",
                "required_evidence": list(evidence),
                "missing_evidence": missing,
            }
        )
    return {
        "name": "molecule-ranker pilot usability",
        "checks": checks,
        "passed_count": sum(1 for check in checks if check["status"] == "pass"),
        "failed_count": sum(1 for check in checks if check["status"] == "fail"),
    }


def explain_error(error_code: str) -> dict[str, str]:
    normalized = error_code.strip().lower().replace("_", "-")
    return ERROR_EXPLANATIONS.get(
        normalized,
        {
            "title": "Unknown error",
            "meaning": "The error code is not recognized by the pilot helper.",
            "remediation": (
                "Check the command output, request ID, job ID, and audit log, then contact "
                "support with a redacted support bundle."
            ),
        },
    )


def next_steps_for_run(run_dir: str | Path) -> list[str]:
    path = Path(run_dir)
    steps = ["Review candidate ranking outputs and source provenance."]
    if not (path / "generated_candidates.json").exists():
        steps.append("generated molecule hypotheses are optional; keep them separate if added.")
    else:
        steps.append("Review generated molecule hypotheses as computational hypotheses only.")
    if not (path / "review_workspace.json").exists():
        steps.append("Create or open the review workflow for human triage.")
    if not (path / "benchmark_report.json").exists():
        steps.append("Run evaluation only as a platform-quality artifact.")
    steps.append("Capture pilot feedback if the workflow is unclear or blocked.")
    return steps


__all__ = ["explain_error", "next_steps_for_run", "run_usability_checks"]
