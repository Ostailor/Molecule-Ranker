from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from molecule_ranker.campaigns import (
    Campaign,
    CampaignBudget,
    CampaignExecutionEvent,
    CampaignMemo,
    CampaignObjective,
    CampaignPlan,
    CampaignStore,
    CampaignWorkPackage,
    ReplanTrigger,
)
from molecule_ranker.integrations.schemas import ExternalSystem
from molecule_ranker.integrations.store import IntegrationStore
from molecule_ranker.knowledge_graph import (
    GraphEntity,
    GraphProvenance,
    GraphRelation,
    KnowledgeGraph,
    KnowledgeGraphStore,
    MechanismHypothesis,
)
from molecule_ranker.models.registry import ModelRegistry
from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEndpoint,
    ModelEvaluationReport,
    ModelFeatureSpec,
    ModelPrediction,
    ModelTrainingRun,
)
from molecule_ranker.server import create_app
from molecule_ranker.workers import PipelineWorker
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def test_dashboard_pages_require_auth(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    for path in [
        "/dashboard",
        "/dashboard/projects",
        "/dashboard/projects/workspace-a",
        "/dashboard/projects/workspace-a/runs",
        "/dashboard/projects/workspace-a/runs/run-a",
        "/dashboard/projects/workspace-a/runs/run-a/candidates",
        "/dashboard/projects/workspace-a/runs/run-a/generated",
        "/dashboard/projects/workspace-a/runs/run-a/developability",
        "/dashboard/projects/workspace-a/runs/run-a/experiments",
        "/dashboard/projects/workspace-a/runs/run-a/active-learning",
        "/dashboard/projects/workspace-a/structure/target-structures",
        "/dashboard/projects/workspace-a/structure/selection",
        "/dashboard/projects/workspace-a/structure/receptor-preparation",
        "/dashboard/projects/workspace-a/structure/binding-sites",
        "/dashboard/projects/workspace-a/structure/docking-runs",
        "/dashboard/projects/workspace-a/structure/docking-poses",
        "/dashboard/projects/workspace-a/structure/interaction-profiles",
        "/dashboard/projects/workspace-a/structure/assessments",
        "/dashboard/projects/workspace-a/structure/benchmarks",
        "/dashboard/projects/workspace-a/design/plans",
        "/dashboard/projects/workspace-a/design/generator-runs",
        "/dashboard/projects/workspace-a/design/oracle-scores",
        "/dashboard/projects/workspace-a/design/readiness",
        "/dashboard/projects/workspace-a/design/benchmarks",
        "/dashboard/projects/workspace-a/design/active-loop",
        "/dashboard/projects/workspace-a/models",
        "/dashboard/projects/workspace-a/models/model-1",
        "/dashboard/projects/workspace-a/models/training-runs",
        "/dashboard/projects/workspace-a/models/evaluation-reports",
        "/dashboard/projects/workspace-a/models/calibration",
        "/dashboard/projects/workspace-a/models/prediction-batches",
        "/dashboard/projects/workspace-a/models/predictions/Candidate%201",
        "/dashboard/projects/workspace-a/models/active-design-influence",
        "/dashboard/projects/workspace-a/activity",
        "/dashboard/projects/workspace-a/review",
        "/dashboard/projects/workspace-a/portfolio",
        "/dashboard/projects/workspace-a/portfolio/candidates",
        "/dashboard/projects/workspace-a/portfolio/optimization-runs",
        "/dashboard/projects/workspace-a/portfolio/scenarios",
        "/dashboard/projects/workspace-a/portfolio/selected",
        "/dashboard/projects/workspace-a/portfolio/rejected-deferred",
        "/dashboard/projects/workspace-a/portfolio/stage-gates",
        "/dashboard/projects/workspace-a/portfolio/batches",
        "/dashboard/projects/workspace-a/portfolio/memos",
        "/dashboard/projects/workspace-a/portfolio/audit",
        "/dashboard/projects/workspace-a/knowledge-graph",
        "/dashboard/projects/workspace-a/knowledge-graph/search",
        "/dashboard/projects/workspace-a/knowledge-graph/targets/target%3AMAOB",
        "/dashboard/projects/workspace-a/knowledge-graph/molecules/molecule%3Arasagiline",
        (
            "/dashboard/projects/workspace-a/knowledge-graph/generated-molecules/"
            "generated_molecule%3Agen1"
        ),
        "/dashboard/projects/workspace-a/knowledge-graph/mechanisms/mechanism%3Amaob",
        "/dashboard/projects/workspace-a/knowledge-graph/contradictions",
        "/dashboard/projects/workspace-a/knowledge-graph/staleness",
        "/dashboard/projects/workspace-a/knowledge-graph/recommendations",
        "/dashboard/projects/workspace-a/knowledge-graph/query",
        "/dashboard/projects/workspace-a/knowledge-graph/portfolio",
        "/dashboard/projects/workspace-a/hypotheses",
        "/dashboard/projects/workspace-a/hypotheses/hypothesis-generated-1",
        "/dashboard/projects/workspace-a/hypotheses/evidence-gaps",
        "/dashboard/projects/workspace-a/hypotheses/research-questions",
        "/dashboard/projects/workspace-a/hypotheses/falsification-criteria",
        "/dashboard/projects/workspace-a/hypotheses/contradictions",
        "/dashboard/projects/workspace-a/hypotheses/review-queue",
        "/dashboard/projects/workspace-a/hypotheses/lifecycle",
        "/dashboard/projects/workspace-a/campaigns",
        "/dashboard/projects/workspace-a/campaigns/campaign-1",
        "/dashboard/projects/workspace-a/campaigns/plan",
        "/dashboard/projects/workspace-a/campaigns/work-packages",
        "/dashboard/projects/workspace-a/campaigns/budget",
        "/dashboard/projects/workspace-a/campaigns/dependencies",
        "/dashboard/projects/workspace-a/campaigns/stage-gates",
        "/dashboard/projects/workspace-a/campaigns/replan-triggers",
        "/dashboard/projects/workspace-a/campaigns/memo",
        "/dashboard/projects/workspace-a/campaigns/audit",
        "/dashboard/projects/workspace-a/candidates/Rasagiline",
        "/dashboard/projects/workspace-a/codex",
        "/dashboard/notifications",
        "/dashboard/audit",
        "/dashboard/admin",
        "/dashboard/admin/users",
        "/dashboard/admin/organizations",
        "/dashboard/admin/teams",
        "/dashboard/admin/memberships",
        "/dashboard/admin/service-accounts",
        "/dashboard/admin/audit",
        "/dashboard/admin/jobs",
        "/dashboard/admin/health",
        "/dashboard/admin/codex-worker",
    ]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_login_page_renders_research_use_form(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.get("/login")

    assert response.status_code == 200
    assert "molecule-ranker" in response.text
    assert 'name="email"' in response.text
    assert 'name="password"' in response.text
    assert "Research use only" in response.text


def test_dashboard_core_pages_render_project_run_and_research_views(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard": ["Projects", "Research"],
        "/dashboard/projects": ["Projects", "workspace-a"],
        "/dashboard/projects/workspace-a": [
            "Research boundaries",
            "Portfolio analytics",
            "Program overview",
            "Runs",
        ],
        "/dashboard/projects/workspace-a/runs": ["Runs", "Parkinson disease"],
        "/dashboard/projects/workspace-a/runs/run-a": ["Run views", "Experimental results"],
        "/dashboard/projects/workspace-a/runs/run-a/candidates": [
            "Candidate ranking table",
            "Rasagiline",
        ],
        "/dashboard/projects/workspace-a/runs/run-a/developability": [
            "Developability view",
            "computational screening",
        ],
        "/dashboard/projects/workspace-a/runs/run-a/experiments": [
            "Experimental evidence",
            "Model predictions",
            "Model cards",
            "not experimental evidence",
        ],
        "/dashboard/projects/workspace-a/runs/run-a/active-learning": [
            "Active learning view",
            "measure selectivity",
        ],
        "/dashboard/projects/workspace-a/design/plans": ["Design plans", "design-plan-1"],
        "/dashboard/projects/workspace-a/design/generator-runs": [
            "Generator ensemble runs",
            "Computational hypothesis",
        ],
        "/dashboard/projects/workspace-a/design/oracle-scores": [
            "Oracle scores",
            "experiment_worthiness_score",
        ],
        "/dashboard/projects/workspace-a/design/readiness": [
            "Experiment-readiness queue",
            "Computational hypothesis",
        ],
        "/dashboard/projects/workspace-a/design/benchmarks": [
            "Design benchmark reports",
            "Validity",
        ],
        "/dashboard/projects/workspace-a/design/active-loop": [
            "Active design loop history",
            "design_loop_report.md",
        ],
        "/dashboard/projects/workspace-a/structure/target-structures": [
            "Target structures",
            "RCSB_PDB:1ABC",
            "Structure reports cannot be interpreted as binding evidence",
        ],
        "/dashboard/projects/workspace-a/structure/selection": [
            "Structure selection",
            "selection-1",
        ],
        "/dashboard/projects/workspace-a/structure/receptor-preparation": [
            "Receptor preparation",
            "metadata_only",
        ],
        "/dashboard/projects/workspace-a/structure/binding-sites": [
            "Binding sites",
            "co_crystal_ligand",
        ],
        "/dashboard/projects/workspace-a/structure/docking-runs": [
            "Docking runs",
            "computational workflow",
            "Docking scores do not prove binding",
        ],
        "/dashboard/projects/workspace-a/structure/docking-poses": [
            "Docking poses",
            "computational hypotheses",
            "pose-1",
        ],
        "/dashboard/projects/workspace-a/structure/interaction-profiles": [
            "Interaction profiles",
            "computational pose annotations",
        ],
        "/dashboard/projects/workspace-a/structure/assessments": [
            "Structure-aware assessments",
            "not binding evidence",
        ],
        "/dashboard/projects/workspace-a/structure/benchmarks": [
            "Structure benchmark reports",
            "pose_qc_pass_rate",
        ],
        "/dashboard/projects/workspace-a/review": ["Review queue", "Rasagiline"],
        "/dashboard/projects/workspace-a/portfolio": [
            "Program overview",
            "advisory until approved",
        ],
        "/dashboard/projects/workspace-a/portfolio/candidates": [
            "Portfolio candidates",
            "Generated count",
        ],
        "/dashboard/projects/workspace-a/portfolio/scenarios": [
            "Scenario analysis",
            "robust under uncertainty",
        ],
        "/dashboard/projects/workspace-a/portfolio/stage-gates": [
            "Stage gates",
            "portfolio:approve_stage_gate",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph": [
            "Cross-program knowledge graph",
            "memory and reasoning layer",
            "does not create biomedical truth",
        ],
    }
    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, path


def test_campaign_dashboard_pages_render_campaign_artifacts(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _seed_campaign_dashboard_store(tmp_path, "workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/projects/workspace-a/campaigns": [
            "Campaign list",
            "campaign-1",
            "research-management guidance",
        ],
        "/dashboard/projects/workspace-a/campaigns/campaign-1": [
            "Campaign detail",
            "selection-1",
            "not lab protocols",
        ],
        "/dashboard/projects/workspace-a/campaigns/plan": [
            "Campaign plan",
            "deterministic campaign plan",
            "wp-generated-review",
        ],
        "/dashboard/projects/workspace-a/campaigns/work-packages": [
            "Work packages",
            "planning objects, not protocols",
            "generated_molecule_review",
            "completed",
        ],
        "/dashboard/projects/workspace-a/campaigns/budget": [
            "Budget/resource view",
            "review hours",
            "planning estimates only",
        ],
        "/dashboard/projects/workspace-a/campaigns/dependencies": [
            "Dependency graph",
            "planning order",
            "wp-generated-review",
        ],
        "/dashboard/projects/workspace-a/campaigns/stage-gates": [
            "Stage gates",
            "generated_molecule_review",
            "campaign:approve",
        ],
        "/dashboard/projects/workspace-a/campaigns/replan-triggers": [
            "Replan triggers",
            "contradiction_detected",
        ],
        "/dashboard/projects/workspace-a/campaigns/memo": [
            "Campaign memo",
            "assistant output",
            "separate from the deterministic campaign plan",
        ],
        "/dashboard/projects/workspace-a/campaigns/audit": [
            "Campaign audit timeline",
            "stage_gate_decided",
        ],
    }
    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, path
        assert "incubation" not in response.text.lower(), path
        assert "synthesis route" not in response.text.lower(), path


def test_knowledge_graph_dashboard_pages_show_labels_provenance_and_escape_html(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _seed_knowledge_graph(tmp_path)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/projects/workspace-a/knowledge-graph": [
            "Graph overview",
            "Inferred relation",
            "Generated hypothesis",
            "Model prediction, not evidence",
            "Codex graph summary, assistant output",
            "/dashboard/projects/workspace-a/artifacts/kg-artifact/download",
            "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/search?q=MAOB": [
            "Entity search",
            "MAOB",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/targets/target%3AMAOB": [
            "Target graph page",
            "Graph edges",
            "Inferred edge: graph hypothesis, not EvidenceItem",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/molecules/molecule%3Arasagiline": [
            "Molecule graph page",
            "Source-backed relation",
            "Provenance links",
        ],
        (
            "/dashboard/projects/workspace-a/knowledge-graph/generated-molecules/"
            "generated_molecule%3Agen1"
        ): [
            "Generated molecule graph page",
            "Generated hypothesis",
            "hypothesis, not EvidenceItem",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/mechanisms/mechanism%3Amaob": [
            "Mechanism hypothesis page",
            "hypothesis, not proof",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/contradictions": [
            "Contradiction report",
            "advisory",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/staleness": [
            "Staleness report",
            "preserves temporal provenance",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/recommendations": [
            "Cross-program recommendations",
            "contradiction_to_resolve",
            "advisory graph-derived summaries",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/query": [
            "Graph query explorer",
            "graph-derived summaries, not new evidence",
        ],
        "/dashboard/projects/workspace-a/knowledge-graph/portfolio": [
            "Portfolio graph view",
            "decision-memory context only",
        ],
    }

    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, path
        assert "<script>alert(\"x\")</script>" not in response.text


def test_model_dashboard_pages_label_predictions_and_uncalibrated_warnings(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _seed_model_registry(tmp_path)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/projects/workspace-a/models": [
            "Model registry",
            "binary local surrogate",
            "Calibration status",
        ],
        "/dashboard/projects/workspace-a/models/model-1": [
            "Model card detail",
            "Limitations",
            "Calibration status",
            "Uncalibrated warning shown",
        ],
        "/dashboard/projects/workspace-a/models/training-runs": [
            "Training runs",
            "skipped_insufficient_data",
        ],
        "/dashboard/projects/workspace-a/models/evaluation-reports": [
            "Evaluation reports",
            "Leakage checks",
        ],
        "/dashboard/projects/workspace-a/models/calibration": [
            "Calibration summary",
            "Uncalibrated warning shown",
        ],
        "/dashboard/projects/workspace-a/models/prediction-batches": [
            "Prediction batches",
            "Prediction artifacts are separate",
            "Model predictions are predictions",
            "Prediction artifact contents",
            "Candidate 1",
            "surrogate_positive (prediction only)",
            "Uncalibrated prediction warning shown",
            "Out-of-domain prediction warning shown",
        ],
        "/dashboard/projects/workspace-a/models/predictions/Candidate%201": [
            "Prediction detail for candidate",
            "prediction only",
            "not experimental evidence",
        ],
        "/dashboard/projects/workspace-a/models/active-design-influence": [
            "Model influence in active design",
            "prioritization rationale",
            "Calibrated surrogate oracle",
            "model uncertainty remains auditable",
        ],
    }
    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, path


def test_model_dashboard_permission_enforced(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    created = client.post(
        "/admin/users",
        json={"email": "outsider@example.com", "password": "Outsider-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.cookies.clear()
    _web_login(client, "outsider@example.com", "Outsider-password-1")

    response = client.get("/dashboard/projects/workspace-a/models")

    assert response.status_code == 403


def test_design_dashboard_labels_generated_molecules_as_hypotheses(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/design/readiness")

    assert response.status_code == 200
    assert "Computational hypothesis" in response.text
    assert "not proven activity" in response.text
    assert "No synthesis instructions" in response.text


def test_pose_file_artifact_download_requires_structure_export(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    pose_path = tmp_path / "pose-file.pdb"
    pose_path.write_text("POSE\n")
    _append_workspace_artifact(
        tmp_path,
        pose_path,
        artifact_id="pose-file",
        artifact_type="docking_pose",
    )
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    viewer_id = created.json()["user"]["user_id"]
    client.post(
        "/projects/workspace-a/share",
        json={"role": "viewer", "user_id": viewer_id},
        headers=admin_headers,
    )
    viewer_headers = _api_login(client, "viewer@example.com", "Viewer-password-1")

    blocked = client.get(
        "/projects/workspace-a/artifacts/pose-file/download",
        headers=viewer_headers,
    )
    allowed = client.get(
        "/projects/workspace-a/artifacts/pose-file/download",
        headers=admin_headers,
    )

    assert blocked.status_code == 403
    assert "structure:export" in blocked.text
    assert allowed.status_code == 200
    assert allowed.text == "POSE\n"


def test_first_run_dashboard_explains_setup_defaults_and_project_creation(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "First-run setup" in response.text
    assert "Create project" in response.text
    assert "Generation is disabled by default" in response.text
    assert "Docking is disabled by default" in response.text
    assert "External writes are disabled by default" in response.text
    assert "Codex is disabled until configured" in response.text
    assert "Integrations default to dry-run/read-only" in response.text
    assert 'name="workspace_id"' in response.text


def test_dashboard_project_creation_form_creates_owned_project(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")
    csrf_token = client.cookies.get("mr_csrf_token")
    assert csrf_token is not None

    created = client.post(
        "/dashboard/projects/create",
        data={
            "csrf_token": csrf_token,
            "workspace_id": "workspace-created",
            "name": "Created project",
        },
        follow_redirects=False,
    )
    detail = client.get("/dashboard/projects/workspace-created")
    portfolio = client.get("/dashboard/projects/workspace-created/portfolio")

    assert created.status_code == 303
    assert created.headers["location"] == "/dashboard/projects/workspace-created"
    assert detail.status_code == 200
    assert "Created project" in detail.text
    assert portfolio.status_code == 200
    assert "<td>Runs</td><td>0</td>" in portfolio.text
    assert "<td>Candidate artifacts</td><td>0</td>" in portfolio.text
    assert "<td>Generated hypotheses</td><td>0</td>" in portfolio.text


def test_portfolio_dashboard_lists_hosted_job_artifacts(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/projects",
        json={"workspace_id": "workspace-created", "name": "Created project"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    queued = client.post(
        "/projects/workspace-created/portfolio/jobs",
        json={
            "job_type": "portfolio_optimize",
            "config": {
                "algorithm": "greedy",
                "max_candidates": 1,
                "candidates": [
                    {
                        "portfolio_candidate_id": "pc-live",
                        "candidate_name": "PC Live",
                        "origin": "existing",
                        "target_symbols": ["TGT1"],
                        "evidence_score": 0.8,
                        "developability_score": 0.7,
                    }
                ],
            },
        },
        headers=admin_headers,
    )
    assert queued.status_code == 200, queued.text
    app = cast(Any, client.app)
    finished = PipelineWorker(
        database=app.state.platform_database,
        root_dir=tmp_path,
    ).run_once()
    assert finished is not None
    artifact_id = finished.result_artifact_ids[0]

    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")
    page = client.get("/dashboard/projects/workspace-created/portfolio/optimization-runs")
    download = client.get(
        f"/dashboard/projects/workspace-created/artifacts/{artifact_id}/download"
    )

    assert page.status_code == 200
    assert artifact_id in page.text
    assert "portfolio_optimize" in page.text
    assert download.status_code == 200
    assert download.headers["x-artifact-id"] == artifact_id
    assert download.json()["portfolio_boundary"] == "advisory_until_approved"


def test_admin_dashboard_pages_render_for_admin(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/admin": "Admin controls",
        "/dashboard/admin/users": "Users",
        "/dashboard/admin/organizations": "Organizations",
        "/dashboard/admin/teams": "Teams",
        "/dashboard/admin/memberships": "Memberships",
        "/dashboard/admin/service-accounts": "Service accounts",
        "/dashboard/admin/audit": "Audit log",
        "/dashboard/admin/jobs": "Job queue",
        "/dashboard/admin/health": "System health",
        "/dashboard/admin/codex-worker": "Codex worker status",
    }
    for path, snippet in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert snippet in response.text


def test_artifact_download_requires_permission_and_serves_safe_artifact(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    artifact_path = tmp_path / "safe-artifact.txt"
    artifact_path.write_text("registered artifact\n")
    _append_workspace_artifact(tmp_path, artifact_path, artifact_id="safe-artifact")

    unauthenticated = client.get("/projects/workspace-a/artifacts/safe-artifact/download")
    downloaded = client.get(
        "/projects/workspace-a/artifacts/safe-artifact/download",
        headers=admin_headers,
    )

    assert unauthenticated.status_code == 401
    assert downloaded.status_code == 200
    assert downloaded.text == "registered artifact\n"
    assert downloaded.headers["X-Artifact-ID"] == "safe-artifact"


def test_project_detail_makes_artifact_downloads_clear_for_browser_users(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    artifact_path = tmp_path / "safe-artifact.txt"
    artifact_path.write_text("registered artifact\n")
    _append_workspace_artifact(tmp_path, artifact_path, artifact_id="safe-artifact")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    detail = client.get("/dashboard/projects/workspace-a")
    downloaded = client.get("/dashboard/projects/workspace-a/artifacts/safe-artifact/download")

    assert detail.status_code == 200
    assert "Artifact downloads" in detail.text
    assert "Download" in detail.text
    assert "/dashboard/projects/workspace-a/artifacts/safe-artifact/download" in detail.text
    assert downloaded.status_code == 200
    assert downloaded.text == "registered artifact\n"
    assert downloaded.headers["X-Artifact-ID"] == "safe-artifact"


def test_admin_jobs_page_explains_status_and_errors(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    queued = app.state.platform_database.enqueue_job(
        job_type="dashboard_build",
        requested_by_user_id=admin.user_id,
        project_id="workspace-a",
    )
    failed = app.state.platform_database.enqueue_job(
        job_type="codex_task",
        requested_by_user_id=admin.user_id,
        project_id="workspace-a",
    )
    failed.status = "failed"
    failed.error = "Codex provider is not configured."
    app.state.platform_database.update_job(failed)
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/admin/jobs")

    assert response.status_code == 200
    assert "Status guide" in response.text
    assert "Queued means waiting for a worker" in response.text
    assert queued.job_id in response.text
    assert failed.job_id in response.text
    assert "Codex provider is not configured." in response.text


def test_dashboard_rbac_hides_unauthorized_projects(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.cookies.clear()

    _web_login(client, "viewer@example.com", "Viewer-password-1")
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "workspace-a" not in response.text
    assert "No authorized projects" in response.text


def test_dashboard_artifact_path_traversal_is_blocked(tmp_path: Path) -> None:
    outside = tmp_path.parent / "dashboard-outside-secret.txt"
    outside.write_text("secret")
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _append_workspace_artifact(tmp_path, outside, artifact_id="outside-secret")

    outside_response = client.get(
        "/projects/workspace-a/artifacts/outside-secret/download",
        headers=admin_headers,
    )
    traversal_response = client.get(
        "/projects/workspace-a/artifacts/..%2F.env/download",
        headers=admin_headers,
    )

    assert outside_response.status_code == 403
    assert traversal_response.status_code in {403, 404}


def test_dashboard_escapes_user_provided_html(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(
        client,
        tmp_path,
        admin_headers,
        project_id="workspace-a",
        candidate_name="<script>alert(1)</script>",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/candidates")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_dashboard_cookie_logout_requires_csrf_token(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")
    csrf_token = client.cookies.get("mr_csrf_token")
    assert csrf_token is not None

    blocked = client.post("/logout", follow_redirects=False)
    allowed = client.post(
        "/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 303
    assert allowed.headers["location"] == "/login"


def test_service_account_token_cannot_access_browser_dashboard(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    app_state = cast(Any, client.app).state
    admin = app_state.platform_database.list_users()[0]
    service_user = app_state.platform_database.create_user(
        email="svc@example.com",
        password="Service-password-1",
    )
    token = "mrs_service_token_for_dashboard_test"
    app_state.platform_database.create_service_account_token(
        name="automation",
        token=token,
        user_id=service_user.user_id,
        created_by_user_id=admin.user_id,
        scopes=["project:read"],
    )

    response = client.get(
        "/dashboard",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_generated_molecules_are_labeled_as_hypotheses(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/generated")

    assert response.status_code == 200
    assert "Generated molecules are computational hypotheses" in response.text
    assert "not validated actives" in response.text
    assert "No synthesis instructions" in response.text
    assert "Computational hypothesis" in response.text
    assert "Hypothesis-1" in response.text


def test_review_queue_is_actionable_and_marks_optional_workflow(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/review")

    assert response.status_code == 200
    assert "Review workflow is optional" in response.text
    assert "Open dossier" in response.text
    assert "Pending review items" in response.text
    assert "Reviewer comments are separate from model scores" in response.text


def test_codex_outputs_are_separate_from_evidence(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _write_codex_output(tmp_path)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/codex")

    assert response.status_code == 200
    assert "Codex-generated summaries are assistant outputs, not evidence" in response.text
    assert "Codex is disabled until configured" in response.text
    assert "requires codex:run permission" in response.text
    assert "scoped to registered project artifacts" in response.text
    assert "Codex-generated summaries" in response.text
    assert "Assistant summary grounded in artifacts." in response.text


def test_integration_dashboard_makes_dry_run_and_write_modes_obvious(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    store = IntegrationStore(app.state.platform_database, user=admin)
    store.create_external_system(
        ExternalSystem(
            external_system_id="dry-run-system",
            name="Dry run system",
            system_type="generic_rest",
            default_mode="dry_run",
        )
    )
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/integrations")

    assert response.status_code == 200
    assert "Dry-run/read-only by default" in response.text
    assert "Write-enabled requires explicit permission" in response.text
    assert "mode-badge dry-run" in response.text
    assert "Dry run system" in response.text


def test_admin_audit_page_is_accessible_and_explains_review_use(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    app.state.platform_database.write_audit(
        "release_check",
        actor_user_id=admin.user_id,
        summary="Admin reviewed release evidence.",
    )
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/admin/audit")

    assert response.status_code == 200
    assert "Audit log" in response.text
    assert "Admins can review security, access, job, and export events here." in response.text
    assert "release_check" in response.text


def test_dashboard_error_states_are_understandable(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/missing-project")

    assert response.status_code == 404
    assert "Project not found." in response.text
    assert "What happened" in response.text
    assert "Request ID" in response.text


def test_codex_output_is_safely_escaped(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _write_codex_output(tmp_path, output_text="<script>alert(1)</script> Codex summary")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/codex")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Codex summary" in response.text


def test_codex_dry_run_output_is_summarized(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _write_codex_output(
        tmp_path,
        output_text=json.dumps(
            {
                "command": ["codex", "exec", "--json"],
                "dry_run": True,
                "prompt": "large guarded prompt with forbidden_commands metadata",
            }
        ),
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/codex")

    assert response.status_code == 200
    assert "Dry-run Codex request prepared; no live Codex execution." in response.text
    assert "forbidden_commands metadata" not in response.text


def test_uploaded_assay_file_names_are_escaped(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(
        client,
        tmp_path,
        admin_headers,
        project_id="workspace-a",
        assay_file_name="<script>assay</script>.csv",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/experiments")

    assert response.status_code == 200
    assert "<script>assay</script>.csv" not in response.text
    assert "\\u003cscript\\u003eassay\\u003c/script\\u003e.csv" in response.text


def test_audit_log_inaccessible_to_non_admin(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.cookies.clear()
    _web_login(client, "viewer@example.com", "Viewer-password-1")

    dashboard_audit = client.get("/dashboard/audit")
    admin_audit = client.get("/dashboard/admin/audit")

    assert dashboard_audit.status_code == 403
    assert admin_audit.status_code == 403


def test_candidate_comments_are_separate_from_evidence(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    app.state.platform_database.add_project_comment(
        project_id="workspace-a",
        author_user_id=admin.user_id,
        body="Team note: review selectivity rationale.",
        object_type="candidate",
        object_id="Rasagiline",
        candidate_id="Rasagiline",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/candidates/Rasagiline")

    assert response.status_code == 200
    assert "Source-backed evidence" in response.text
    assert "Team comments" in response.text
    assert "Comments are collaboration notes, not biomedical evidence" in response.text
    assert "Team note: review selectivity rationale." in response.text


def _app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _api_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _web_login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/dashboard"


def _create_project_with_run(
    client: TestClient,
    tmp_path: Path,
    headers: dict[str, str],
    *,
    project_id: str,
    candidate_name: str = "Rasagiline",
    assay_file_name: str = "assay-results.csv",
) -> None:
    created = client.post(
        "/projects",
        json={"workspace_id": project_id, "name": "Research"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    _write_run(tmp_path / "run-a", candidate_name=candidate_name, assay_file_name=assay_file_name)
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    store.register_run_dir(tmp_path / "run-a", run_id="run-a", workspace=workspace)


def _seed_campaign_dashboard_store(tmp_path: Path, project_id: str) -> None:
    store = CampaignStore(
        tmp_path / ".molecule-ranker" / "campaigns" / project_id / "campaigns.sqlite"
    )
    now = datetime.now(UTC)
    campaign = Campaign(
        campaign_id="campaign-1",
        project_id=project_id,
        program_id="program-1",
        name="Generated candidate review campaign",
        description="Research-management planning artifact.",
        disease_focus=["Parkinson disease"],
        target_focus=["MAOB"],
        hypothesis_ids=["hypothesis-1"],
        portfolio_selection_ids=["selection-1"],
        status="under_review",
        created_at=now,
        updated_at=now,
        metadata={},
    )
    store.create_campaign(campaign)
    objective = CampaignObjective(
        objective_id="objective-1",
        campaign_id=campaign.campaign_id,
        name="Review generated candidate",
        objective_type="validate_hypothesis",
        linked_hypothesis_ids=["hypothesis-1"],
        linked_candidate_ids=["candidate-generated-1"],
        success_criteria=["Human review decision recorded."],
        stop_criteria=["Critical safety or developability concern remains unresolved."],
        priority_weight=0.8,
        metadata={"portfolio_selection_ids": ["selection-1"]},
    )
    work_package = CampaignWorkPackage(
        work_package_id="wp-generated-review",
        campaign_id=campaign.campaign_id,
        objective_ids=[objective.objective_id],
        package_type="expert_review",
        title="Generated candidate review",
        description="High-level review of computational hypothesis artifacts.",
        linked_candidate_ids=["candidate-generated-1"],
        linked_hypothesis_ids=["hypothesis-1"],
        high_level_activity_category="generated_molecule_review",
        dependencies=[],
        required_approvals=["generated_molecule_review"],
        estimated_cost=None,
        cost_units="relative_units",
        estimated_review_hours=2.0,
        estimated_compute_units=1.0,
        estimated_assay_slots=0,
        status="proposed",
        blocking_reasons=[],
        warnings=["Generated molecule follow-up requires review gate."],
        metadata={"generated_molecule": True},
    )
    budget = CampaignBudget(
        budget_id="budget-1",
        campaign_id=campaign.campaign_id,
        max_total_cost=None,
        cost_units="relative_units",
        max_assay_slots=2,
        max_review_hours=4.0,
        max_compute_units=10.0,
        max_codex_tasks=1,
        max_external_sync_jobs=1,
        reserved_budget={},
        metadata={},
    )
    gate = {
        "gate_id": "gate-generated-review",
        "campaign_id": campaign.campaign_id,
        "work_package_id": work_package.work_package_id,
        "gate_type": "generated_molecule_review",
        "required_role": ["scientific_reviewer"],
        "required_permissions": ["campaign:approve"],
        "required_artifacts": ["hypothesis-1", "candidate-generated-1"],
        "required_review_decisions": ["generated_molecule_review_decision"],
        "blocking_conditions": ["generated_molecule_human_review_required"],
        "approval_status": "pending",
        "rationale": "Generated molecule follow-up requires review.",
        "audit_event": None,
        "metadata": {"not_codex_approvable": True},
    }
    plan = CampaignPlan(
        campaign_plan_id="campaign-plan-1",
        campaign_id=campaign.campaign_id,
        objectives=[objective],
        work_packages=[work_package],
        budget=budget,
        stage_gates=[gate],
        dependency_graph={"nodes": ["wp-generated-review"], "edges": []},
        expected_learning_value=0.7,
        risk_summary={"generated_molecule_review_required": True},
        uncertainty_summary={"model_uncertainty": "moderate"},
        budget_summary={"review_hours": 2.0, "assay_slots": 0},
        recommended_sequence=["wp-generated-review"],
        replan_triggers=["contradiction_detected"],
        human_approval_required=True,
        warnings=["Selected candidates are not proven active, safe, or effective."],
        created_at=now,
        metadata={},
    )
    store.save_campaign_plan(plan)
    store.add_stage_gate_decision(gate)
    store.add_replan_trigger(
        ReplanTrigger(
            trigger_id="trigger-1",
            campaign_id=campaign.campaign_id,
            trigger_type="contradiction_detected",
            severity="high",
            description="Graph contradiction changed planning priority.",
            linked_entity_ids=["hypothesis-1"],
            recommended_action="replan",
            metadata={},
        )
    )
    store.save_campaign_memo(
        CampaignMemo(
            memo_id="memo-1",
            campaign_id=campaign.campaign_id,
            title="Campaign memo",
            executive_summary="Assistant output summary from deterministic artifacts.",
            objectives_summary="Review generated candidate hypothesis.",
            selected_work_packages=[work_package.work_package_id],
            budget_summary="Uses review hours and no assay slots.",
            key_tradeoffs=["Review gate before follow-up."],
            risks=["Generated candidate remains computational hypothesis."],
            uncertainty_notes=["Model uncertainty remains unresolved."],
            replan_triggers=["contradiction_detected"],
            approvals_required=["generated_molecule_review"],
            limitations=["Not a lab protocol.", "No synthesis instructions.", "No dosing."],
            created_at=now,
            metadata={"assistant_output": True, "codex": True},
        )
    )
    store.add_execution_event(
        CampaignExecutionEvent(
            event_id="campaign-event-1",
            campaign_id=campaign.campaign_id,
            work_package_id=work_package.work_package_id,
            event_type="stage_gate_decided",
            actor="reviewer-1",
            timestamp=now,
            summary="Generated molecule review gate recorded.",
            before={"approval_status": "pending"},
            after={"approval_status": "approved"},
            metadata={},
        )
    )
    store.update_work_package_status(
        "wp-generated-review",
        "completed",
        actor="mock_external_integration",
        rationale="Mock external workflow status completed.",
    )


def _seed_knowledge_graph(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    graph = KnowledgeGraph(
        graph_id="kg-dashboard-test",
        entities=[
            GraphEntity(
                entity_id="disease:pd",
                entity_type="disease",
                name="Parkinson disease",
                source_artifact_ids=["kg-artifact"],
            ),
            GraphEntity(
                entity_id="target:MAOB",
                entity_type="target",
                name="MAOB",
                identifiers={"uniprot": "P27338"},
                source_artifact_ids=["kg-artifact"],
            ),
            GraphEntity(
                entity_id="target:xss",
                entity_type="target",
                name='<script>alert("x")</script>',
            ),
            GraphEntity(
                entity_id="molecule:rasagiline",
                entity_type="molecule",
                name="Rasagiline",
                identifiers={"chembl": "CHEMBL887"},
                provenance_refs=["prov:chembl"],
                source_artifact_ids=["kg-artifact"],
            ),
            GraphEntity(
                entity_id="generated_molecule:gen1",
                entity_type="generated_molecule",
                name="Generated MAOB follow-up",
                source_artifact_ids=["kg-artifact"],
            ),
            GraphEntity(
                entity_id="model_prediction:pred1",
                entity_type="model_prediction",
                name="MAOB surrogate prediction",
                source_artifact_ids=["model-artifact"],
            ),
            GraphEntity(
                entity_id="assay_result:positive",
                entity_type="assay_result",
                name="Positive assay",
            ),
            GraphEntity(
                entity_id="assay_result:negative",
                entity_type="assay_result",
                name="Negative assay",
            ),
            GraphEntity(
                entity_id="scaffold:indane",
                entity_type="scaffold",
                name="Indane scaffold",
            ),
            GraphEntity(
                entity_id="developability_alert:alert1",
                entity_type="developability_alert",
                name="High hERG risk",
            ),
            GraphEntity(
                entity_id="portfolio:current",
                entity_type="portfolio",
                name="Current portfolio",
            ),
            GraphEntity(
                entity_id="review_decision:old",
                entity_type="review_decision",
                name="Old accept decision",
            ),
            GraphEntity(
                entity_id="codex_summary:kg",
                entity_type="codex_summary",
                name="Assistant-generated graph summary",
                metadata={"codex_summary": True},
            ),
        ],
        relations=[
            GraphRelation(
                relation_id="rel-disease-target",
                subject_entity_id="disease:pd",
                predicate="associated_with",
                object_entity_id="target:MAOB",
                relation_type="evidence_backed",
                confidence=0.8,
                source_artifact_ids=["kg-artifact"],
                source_record_ids=["ot-record-1"],
            ),
            GraphRelation(
                relation_id="rel-mol-target",
                subject_entity_id="molecule:rasagiline",
                predicate="targets",
                object_entity_id="target:MAOB",
                relation_type="evidence_backed",
                confidence=0.82,
                source_artifact_ids=["kg-artifact"],
                source_record_ids=["chembl-record-1"],
            ),
            GraphRelation(
                relation_id="rel-generated-hypothesis",
                subject_entity_id="generated_molecule:gen1",
                predicate="hypothesizes",
                object_entity_id="target:MAOB",
                relation_type="inferred",
                confidence=0.55,
                source_artifact_ids=["kg-artifact"],
                source_record_ids=["generation-record-1"],
            ),
            GraphRelation(
                relation_id="rel-no-direct-evidence",
                subject_entity_id="generated_molecule:gen1",
                predicate="has_no_direct_evidence",
                object_entity_id="target:MAOB",
                relation_type="inferred",
                confidence=0.9,
                source_artifact_ids=["kg-artifact"],
            ),
            GraphRelation(
                relation_id="rel-model-prediction",
                subject_entity_id="model_prediction:pred1",
                predicate="predicted_by_model",
                object_entity_id="generated_molecule:gen1",
                relation_type="model_prediction",
                confidence=0.7,
                source_artifact_ids=["model-artifact"],
                source_record_ids=["prediction-1"],
                metadata={"prediction_score": 0.7},
            ),
            GraphRelation(
                relation_id="rel-assay-positive",
                subject_entity_id="molecule:rasagiline",
                predicate="supports",
                object_entity_id="assay_result:positive",
                relation_type="experimental",
                confidence=0.8,
                source_artifact_ids=["assay-artifact"],
                source_record_ids=["assay-positive"],
                created_at=now,
                updated_at=now,
                metadata={
                    "assay_name": "MAOB biochemical",
                    "target_symbol": "MAOB",
                    "outcome": "positive",
                    "qc_status": "passed",
                },
            ),
            GraphRelation(
                relation_id="rel-assay-negative",
                subject_entity_id="molecule:rasagiline",
                predicate="contradicts",
                object_entity_id="assay_result:negative",
                relation_type="experimental",
                confidence=0.75,
                source_artifact_ids=["assay-artifact"],
                source_record_ids=["assay-negative"],
                created_at=now,
                updated_at=now,
                metadata={
                    "assay_name": "MAOB biochemical",
                    "target_symbol": "MAOB",
                    "outcome": "negative",
                    "qc_status": "passed",
                },
            ),
            GraphRelation(
                relation_id="rel-explicit-contradiction",
                subject_entity_id="assay_result:positive",
                predicate="contradicts",
                object_entity_id="assay_result:negative",
                relation_type="experimental",
                confidence=0.75,
                source_artifact_ids=["assay-artifact"],
                source_record_ids=["assay-positive", "assay-negative"],
                metadata={"reason": "assay_positive_vs_negative"},
            ),
            GraphRelation(
                relation_id="rel-scaffold",
                subject_entity_id="molecule:rasagiline",
                predicate="has_scaffold",
                object_entity_id="scaffold:indane",
                relation_type="computational",
                confidence=0.7,
                source_artifact_ids=["kg-artifact"],
            ),
            GraphRelation(
                relation_id="rel-risk",
                subject_entity_id="molecule:rasagiline",
                predicate="has_developability_risk",
                object_entity_id="developability_alert:alert1",
                relation_type="computational",
                confidence=0.6,
                source_artifact_ids=["developability-artifact"],
            ),
            GraphRelation(
                relation_id="rel-portfolio",
                subject_entity_id="molecule:rasagiline",
                predicate="selected_in_portfolio",
                object_entity_id="portfolio:current",
                relation_type="review",
                confidence=0.5,
                source_artifact_ids=["portfolio-artifact"],
            ),
            GraphRelation(
                relation_id="rel-stale",
                subject_entity_id="review_decision:old",
                predicate="stale_due_to",
                object_entity_id="molecule:rasagiline",
                relation_type="inferred",
                confidence=0.7,
                source_artifact_ids=["kg-artifact"],
                source_record_ids=["review-stale-1"],
                metadata={"reason": "review_decision_predates_new_experimental_result"},
            ),
            GraphRelation(
                relation_id="rel-codex",
                subject_entity_id="codex_summary:kg",
                predicate="supports",
                object_entity_id="target:MAOB",
                relation_type="inferred",
                confidence=0.4,
                metadata={"assistant_output": True},
            ),
        ],
        provenance=[
            GraphProvenance(
                provenance_id="prov:codex",
                source_type="codex_summary",
                source_artifact_id="kg-artifact",
                source_record_id="summary-1",
                transformation="Assistant summary of graph paths; not evidence.",
                confidence=0.4,
            )
        ],
        mechanisms=[
            MechanismHypothesis(
                mechanism_id="mechanism:maob",
                disease_entity_id="disease:pd",
                target_entity_ids=["target:MAOB"],
                molecule_entity_ids=["molecule:rasagiline"],
                generated_molecule_entity_ids=["generated_molecule:gen1"],
                evidence_relation_ids=["rel-disease-target", "rel-mol-target"],
                contradiction_relation_ids=["rel-explicit-contradiction"],
                summary="MAOB-linked graph hypothesis for review.",
                support_score=0.7,
                contradiction_score=0.4,
                novelty_score=0.5,
                confidence=0.6,
                status="weakly_supported",
                warnings=["Graph mechanism is not proof of causality."],
            )
        ],
    )
    KnowledgeGraphStore(tmp_path).save(graph)


def _write_run(run_dir: Path, *, candidate_name: str, assay_file_name: str) -> None:
    run_dir.mkdir(parents=True)
    payload = {
        "success": True,
        "disease": {"canonical_name": "Parkinson disease"},
        "targets": [{"symbol": "MAOB"}],
        "candidates": [
            {
                "name": candidate_name,
                "origin": "existing",
                "known_targets": ["MAOB"],
                "score": 0.82,
                "score_breakdown": {"confidence": 0.7},
                "developability_summary": {"risk": "low"},
                "evidence_summary": {"literature": ["source-backed"]},
            }
        ],
        "generated_molecule_hypotheses": [
            {
                "name": "Hypothesis-1",
                "origin": "generated",
                "score": 0.41,
                "rationale": "Computationally generated follow-up hypothesis.",
            }
        ],
        "assay_results": [
            {
                "candidate_name": candidate_name,
                "assay_name": "binding screen",
                "result": "inactive",
                "source_file_name": assay_file_name,
            }
        ],
        "active_learning": {"batch_id": "batch-1", "suggestions": ["measure selectivity"]},
        "summary": {"candidate_count": 1, "generated_candidate_count": 1, "target_count": 1},
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")
    (run_dir / "design_plan.json").write_text(
        json.dumps(
            {
                "design_plan_id": "design-plan-1",
                "disease_name": "Parkinson disease",
                "design_objectives": [{"objective_id": "objective-1", "target_symbol": "MAOB"}],
                "codex_task_result_id": "deterministic-planner-disabled",
            }
        )
    )
    (run_dir / "generated_candidates_v2.json").write_text(
        json.dumps(
            {
                "generated_count": 1,
                "retained_count": 1,
                "retained_generated_molecules": [
                    {
                        "generated_id": "GEN-1",
                        "name": "Design hypothesis 1",
                        "canonical_smiles": "CCO",
                        "origin": "generated",
                        "metadata": {"generator_name": "selfies_mutation"},
                    }
                ],
                "warnings": [],
            }
        )
    )
    (run_dir / "oracle_scores.json").write_text(
        json.dumps(
            {
                "score_name": "experiment_worthiness_score",
                "candidate_count": 1,
                "claim_boundary": "computational triage only",
            }
        )
    )
    (run_dir / "experiment_readiness.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "molecule_id": "GEN-1",
                        "readiness_bucket": "ready_for_expert_review",
                        "blocking_risks": [],
                    }
                ]
            }
        )
    )
    (run_dir / "benchmark_report.json").write_text(
        json.dumps(
            {
                "metrics": {
                    "validity_rate": 1.0,
                    "uniqueness_rate": 1.0,
                    "critical_alert_rate": 0.0,
                }
            }
        )
    )
    (run_dir / "design_loop_report.md").write_text("# design_loop_report.md\n")
    (run_dir / "structures.json").write_text(
        json.dumps(
            {
                "structures": [
                    {
                        "structure_id": "RCSB_PDB:1ABC",
                        "source": "RCSB_PDB",
                        "target_symbol": "MAOB",
                        "structure_type": "experimental",
                    }
                ]
            }
        )
    )
    (run_dir / "structure_selection.json").write_text(
        json.dumps(
            {
                "structure_selection": [
                    {
                        "selection_id": "selection-1",
                        "selected_structure_id": "RCSB_PDB:1ABC",
                        "confidence": 0.8,
                    }
                ]
            }
        )
    )
    (run_dir / "receptor_preparation.json").write_text(
        json.dumps(
            {
                "receptor_preparation": [
                    {
                        "receptor_prep_id": "receptor-1",
                        "structure_id": "RCSB_PDB:1ABC",
                        "preparation_method": "metadata_only",
                    }
                ]
            }
        )
    )
    (run_dir / "binding_sites.json").write_text(
        json.dumps(
            {
                "binding_sites": [
                    {
                        "binding_site_id": "site-1",
                        "method": "co_crystal_ligand",
                        "confidence": 0.7,
                    }
                ]
            }
        )
    )
    (run_dir / "docking_runs.json").write_text(
        json.dumps(
            {
                "docking_runs": [
                    {
                        "docking_run_id": "dock-1",
                        "docking_engine": "null",
                        "status": "skipped",
                    }
                ]
            }
        )
    )
    (run_dir / "docking_poses.json").write_text(
        json.dumps(
            {
                "docking_poses": [
                    {
                        "pose_id": "pose-1",
                        "docking_score": -7.0,
                        "confidence": 0.4,
                    }
                ]
            }
        )
    )
    (run_dir / "interaction_profiles.json").write_text(
        json.dumps(
            {
                "interaction_profiles": [
                    {
                        "profile_id": "profile-1",
                        "interaction_counts": {"hydrophobic": 2},
                    }
                ]
            }
        )
    )
    (run_dir / "structure_aware_assessments.json").write_text(
        json.dumps(
            {
                "structure_aware_assessments": [
                    {
                        "assessment_id": "assessment-1",
                        "recommendation": "needs_structure_review",
                        "consensus_score": 0.42,
                    }
                ]
            }
        )
    )
    (run_dir / "structure_benchmark_report.json").write_text(
        json.dumps({"metrics": {"pose_qc_pass_rate": 0.5}})
    )


def _seed_model_registry(tmp_path: Path) -> None:
    registry = ModelRegistry(
        db_path=tmp_path / ".molecule-ranker/models/model_registry.sqlite",
        artifact_dir=tmp_path / ".molecule-ranker/models/registry_artifacts",
    )
    endpoint = ModelEndpoint(
        endpoint_id="endpoint-binary",
        endpoint_name="binary_endpoint",
        endpoint_category="potency",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        assay_type="biochemical",
        unit=None,
        label_type="binary",
        positive_label="positive",
        directionality="binary",
    )
    feature_spec = ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["molecular_weight"],
        normalization="none",
    )
    card = ModelCard(
        model_id="model-1",
        model_name="binary local surrogate",
        model_version="1.2.0",
        plugin_name="local_sklearn_baseline",
        endpoint=endpoint,
        feature_spec=feature_spec,
        training_dataset_id="dataset-1",
        training_data_summary={"row_count": 4, "source_result_ids": ["result-1"]},
        model_type="LogisticRegression",
        intended_use="Assay-specific prioritization only.",
        limitations=["Predictions are not experimental evidence."],
        metrics={"accuracy": 0.75},
        calibration_metrics={"status": "uncalibrated"},
        applicability_domain_method="nearest_neighbor_tanimoto",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    registry.register_model_card(card)
    registry.register_training_run(
        ModelTrainingRun(
            training_run_id="training-run-1",
            model_id="model-1",
            dataset_id="dataset-1",
            status="skipped_insufficient_data",
            started_at=datetime(2026, 1, 4, tzinfo=UTC),
            completed_at=datetime(2026, 1, 4, tzinfo=UTC),
            metrics={},
            calibration_metrics={"status": "insufficient_data"},
            warnings=["small dataset"],
        )
    )
    registry.register_evaluation_report(
        ModelEvaluationReport(
            evaluation_id="evaluation-1",
            model_id="model-1",
            dataset_id="dataset-1",
            split_strategy="scaffold",
            metrics={"accuracy": 0.75},
            calibration_metrics={"status": "uncalibrated"},
            leakage_checks={"passed": True},
            applicability_domain_summary={"out_of_domain": 1},
            warnings=["calibration validation set too small"],
            created_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    )
    registry.save_prediction_batch(
        "model-1",
        "batch-1",
        [
            ModelPrediction(
                prediction_id="prediction-1",
                model_id="model-1",
                model_version="1.2.0",
                endpoint_id="endpoint-binary",
                candidate_id="candidate-1",
                candidate_name="Candidate 1",
                candidate_origin="generated",
                canonical_smiles="CCO",
                inchi_key=None,
                predicted_value=True,
                predicted_probability=0.6,
                prediction_label="surrogate_positive",
                uncertainty=0.4,
                confidence=0.6,
                applicability_domain="out_of_domain",
                calibration_status="uncalibrated",
                explanation="Prediction artifact only.",
                warnings=["not evidence"],
                created_at=datetime(2026, 1, 6, tzinfo=UTC),
                metadata={"not_experimental_evidence": True},
            )
        ],
    )


def _write_codex_output(
    tmp_path: Path,
    *,
    output_text: str = "Assistant summary grounded in artifacts.",
) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    output_dir = tmp_path / ".molecule-ranker" / "codex_project_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "summarize_project-20260101T000000Z.json"
    output_path.write_text(
        json.dumps(
            {
                "task_type": "summarize_project",
                "workspace_id": workspace.workspace_id,
                "status": "succeeded",
                "output_text": output_text,
            }
        )
    )
    workspace.codex_outputs.append(
        {
            "task_type": "summarize_project",
            "status": "succeeded",
            "path": str(output_path),
            "artifact_refs": [],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    store.save(workspace)


def _append_workspace_artifact(
    tmp_path: Path,
    path: Path,
    *,
    artifact_id: str,
    artifact_type: str = "report",
) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    data = path.read_bytes()
    workspace.artifacts.append(
        ArtifactRecord(
            artifact_id=artifact_id,
            workspace_id=workspace.workspace_id,
            path=str(path.resolve()),
            artifact_type=artifact_type,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
    )
    store.save(workspace)
