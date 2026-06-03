from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.runtime_agents.context import (
    build_runtime_context,
    enforce_context_budget,
    extract_allowed_refs,
    redact_sensitive_context,
    summarize_artifact_for_planning,
)
from molecule_ranker.runtime_agents.schemas import RuntimeAgentSession


def test_unauthorized_artifact_excluded(tmp_path: Path) -> None:
    authorized = tmp_path / "authorized.json"
    authorized.write_text('{"candidate_count": 4, "status": "ready"}', encoding="utf-8")
    secret = tmp_path / "secret.json"
    secret.write_text('{"candidate_count": 99, "status": "restricted"}', encoding="utf-8")
    session = _session(
        {
            "artifact_manifest": [
                {
                    "artifact_id": "artifact-ok",
                    "artifact_type": "ranking",
                    "path": str(authorized),
                    "required_permissions": ["artifact:read"],
                },
                {
                    "artifact_id": "artifact-secret",
                    "artifact_type": "assay",
                    "path": str(secret),
                    "required_permissions": ["assay:raw:read"],
                },
            ],
            "selected_artifact_paths": [str(authorized), str(secret)],
            "user_permissions": ["artifact:read"],
        }
    )

    context = build_runtime_context(session)

    assert [artifact["artifact_id"] for artifact in context["artifact_manifest"]] == [
        "artifact-ok"
    ]
    assert str(secret) not in str(context)
    assert extract_allowed_refs(context) == ["artifact-ok"]


def test_secrets_are_redacted() -> None:
    text = "API_KEY=abc123\npassword: hunter2\nAuthorization: Bearer token-value"

    redacted = redact_sensitive_context(text)

    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "token-value" not in redacted
    assert "[REDACTED]" in redacted


def test_large_artifact_summarized(tmp_path: Path) -> None:
    artifact = tmp_path / "large.json"
    artifact.write_text(
        '{"rows": ['
        + ",".join(f'{{"candidate_id": "c-{index}"}}' for index in range(300))
        + "]}",
        encoding="utf-8",
    )

    summary = summarize_artifact_for_planning(artifact, max_bytes=700)

    assert summary["path"] == str(artifact)
    assert summary["truncated"] is True
    assert summary["size_bytes"] > 700
    assert "c-299" not in str(summary)
    assert len(str(summary)) < 1200


def test_context_includes_tools_and_policy(tmp_path: Path) -> None:
    artifact = tmp_path / "ranking.json"
    artifact.write_text(
        '{"candidate_count": 7, "hypothesis_count": 2, "warnings": ["low evidence"]}',
        encoding="utf-8",
    )
    session = _session(
        {
            "project_summary": {"name": "Kinase triage", "status": "active"},
            "artifact_manifest": [
                {
                    "artifact_id": "ranking-1",
                    "artifact_type": "ranking",
                    "path": str(artifact),
                    "required_permissions": ["artifact:read"],
                    "candidate_count": 7,
                }
            ],
            "selected_artifact_paths": [str(artifact)],
            "user_permissions": ["artifact:read", "run:create"],
            "policy_constraints": ["No invented citations.", "External writes require approval."],
            "recent_job_statuses": [{"job_id": "job-1", "status": "succeeded"}],
            "hypothesis_counts": {"generated": 2},
            "campaign_status": {"status": "planning"},
            "evaluation_status": {"latest": "passed"},
            "known_warnings": ["low evidence"],
        }
    )

    context = build_runtime_context(session)
    budgeted = enforce_context_budget(context, max_bytes=4096)

    assert context["project_summary"]["name"] == "Kinase triage"
    assert "run_ranking" in {tool["tool_name"] for tool in context["available_tools"]}
    assert "create_project" not in {tool["tool_name"] for tool in context["available_tools"]}
    assert "No invented citations." in context["policy_constraints"]
    assert context["candidate_counts"]["ranking-1"] == 7
    assert context["hypothesis_counts"]["generated"] == 2
    assert context["campaign_status"]["status"] == "planning"
    assert context["evaluation_status"]["latest"] == "passed"
    assert context["known_warnings"] == ["low evidence"]
    assert budgeted["context_budget"]["truncated"] is False


def _session(metadata: dict[str, object]) -> RuntimeAgentSession:
    return RuntimeAgentSession(
        session_id="session-1",
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        user_goal="Plan a ranking update.",
        autonomy_level="suggest_only",
        status="created",
        started_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        completed_at=None,
        metadata=metadata,
    )
