from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    ResearchHypothesis,
)
from molecule_ranker.hypotheses.schemas import (
    TestableResearchQuestion as ResearchQuestionSchema,
)
from molecule_ranker.hypotheses.store import HypothesisStore
from molecule_ranker.server import create_app


def test_hypothesis_job_permissions_and_review_guardrails(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers, "workspace-a")
    hypothesis = _seed_hypothesis_store(tmp_path, "workspace-a")
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

    denied = client.post(
        "/projects/workspace-a/hypothesis/jobs",
        json={"job_type": "hypothesis_generate", "max_hypotheses": 5},
        headers=viewer_headers,
    )
    queued = client.post(
        "/projects/workspace-a/hypothesis/jobs",
        json={"job_type": "hypothesis_generate", "max_hypotheses": 5},
        headers=admin_headers,
    )
    unreviewed = client.post(
        f"/projects/workspace-a/hypotheses/{hypothesis.hypothesis_id}/review",
        json={
            "decision": "accept_for_planning",
            "rationale": "Accept generated hypothesis for planning.",
        },
        headers=admin_headers,
    )
    reviewed = client.post(
        f"/projects/workspace-a/hypotheses/{hypothesis.hypothesis_id}/review",
        json={
            "decision": "accept_for_planning",
            "rationale": "Human reviewer accepts for planning with no evidence conversion.",
            "human_review_approved": True,
        },
        headers=admin_headers,
    )

    assert denied.status_code == 403
    assert "hypothesis:generate" in denied.text
    assert queued.status_code == 200, queued.text
    assert queued.json()["job"]["job_type"] == "hypothesis_generate"
    assert queued.json()["hypothesis_boundary"] == "hypotheses_are_not_evidence"
    assert unreviewed.status_code == 400
    assert "explicit human approval" in unreviewed.text
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["review_decision_is_not_evidence"] is True
    app = cast(Any, client.app)
    event_types = [
        event.event_type
        for event in app.state.platform_database.list_audit_events(project_id="workspace-a")
    ]
    assert "hypothesis_review_status_changed" in event_types


def test_hypothesis_dashboard_pages_render_lifecycle_and_generated_warning(
    tmp_path: Path,
) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project(client, admin_headers, "workspace-a")
    hypothesis = _seed_hypothesis_store(tmp_path, "workspace-a")
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/projects/workspace-a/hypotheses": [
            "Hypothesis overview",
            hypothesis.hypothesis_id,
            "Generated hypothesis warning visible",
            "Hypotheses are not evidence",
        ],
        f"/dashboard/projects/workspace-a/hypotheses/{hypothesis.hypothesis_id}": [
            "Hypothesis detail",
            "Lifecycle timeline",
            "human review required",
        ],
        "/dashboard/projects/workspace-a/hypotheses/evidence-gaps": [
            "Evidence gaps",
            "Absence of evidence is not evidence of absence",
        ],
        "/dashboard/projects/workspace-a/hypotheses/research-questions": [
            "Research questions",
            "not lab protocols",
        ],
        "/dashboard/projects/workspace-a/hypotheses/falsification-criteria": [
            "Falsification criteria",
            "not experimental procedures",
        ],
        "/dashboard/projects/workspace-a/hypotheses/contradictions": [
            "Contradictions",
            "review prompts",
        ],
        "/dashboard/projects/workspace-a/hypotheses/review-queue": [
            "Review queue",
            "Codex cannot approve hypotheses",
        ],
        "/dashboard/projects/workspace-a/hypotheses/lifecycle": [
            "Lifecycle timeline",
            "Hypothesis status changes are audited",
            "created",
        ],
    }
    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, path
        assert "synthesis instructions" in response.text
        assert "lab protocols" in response.text


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


def _create_project(client: TestClient, headers: dict[str, str], project_id: str) -> None:
    created = client.post(
        "/projects",
        json={"workspace_id": project_id, "name": "Research"},
        headers=headers,
    )
    assert created.status_code == 200, created.text


def _seed_hypothesis_store(tmp_path: Path, project_id: str) -> ResearchHypothesis:
    store = HypothesisStore(
        tmp_path / ".molecule-ranker" / "hypotheses" / project_id / "hypotheses.sqlite"
    )
    now = datetime.now(UTC)
    hypothesis = ResearchHypothesis(
        hypothesis_id="hypothesis-generated-1",
        hypothesis_type="generated_molecule",
        title="Hypothesis: generated follow-up needs exact evidence",
        statement=(
            "Hypothesis for review: graph-backed generated-molecule context needs "
            "direct imported evidence."
        ),
        generated_molecule_entity_ids=["generated_molecule:gen1"],
        supporting_relation_ids=["rel-generated"],
        source_artifact_ids=["artifact-hypothesis"],
        support_score=0.7,
        contradiction_score=0.3,
        novelty_score=0.6,
        testability_score=0.7,
        uncertainty_score=0.6,
        priority_score=0.68,
        confidence=0.45,
        status="under_review",
        warnings=["Generated molecule has no direct linked evidence."],
        created_at=now,
        updated_at=now,
        metadata={
            "project_id": project_id,
            "not_evidence": True,
            "ranking": {"requires_review_before_follow_up": True},
        },
    )
    store.create_hypothesis(hypothesis)
    store.add_evidence_gap(
        EvidenceGap(
            gap_id="gap-generated-direct",
            hypothesis_id=hypothesis.hypothesis_id,
            gap_type="missing_direct_experimental_result",
            description="No exact generated-molecule result is linked.",
            severity="high",
            suggested_high_level_resolution="High-level review of exact evidence coverage.",
            linked_entity_ids=["generated_molecule:gen1"],
        )
    )
    store.add_falsification_criterion(
        FalsificationCriterion(
            criterion_id="criterion-generated-exact",
            hypothesis_id=hypothesis.hypothesis_id,
            criterion_text=(
                "An exact-structure QC-passed negative result in the intended assay "
                "context would reduce priority for the generated molecule."
            ),
            evidence_type_needed="assay_result",
            decision_impact="decrease_priority",
        )
    )
    store.add_research_question(
        ResearchQuestionSchema(
            question_id="question-generated-direct",
            hypothesis_id=hypothesis.hypothesis_id,
            question_text=(
                "Does high-level evidence for the exact generated molecule reduce "
                "uncertainty for this hypothesis?"
            ),
            question_type="evidence_gap_closure",
            high_level_validation_category="expert review",
            linked_entity_ids=["generated_molecule:gen1"],
            required_context=["Exact generated-molecule evidence context."],
            expected_observation_if_supported="Linked evidence reduces uncertainty.",
            expected_observation_if_not_supported="Evidence remains missing or ambiguous.",
            ambiguity_notes=["The hypothesis remains a planning object, not evidence."],
        )
    )
    return hypothesis
