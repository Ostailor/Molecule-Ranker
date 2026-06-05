from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click import unstyle
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.subagents.executor import MultiAgentRuntimeExecution
from molecule_ranker.subagents.schemas import (
    MultiAgentSession,
    SubagentConsensus,
    SubagentResult,
)
from molecule_ranker.subagents.skills import expand_multi_agent_skill


def test_subagents_cli_help_works() -> None:
    runner = CliRunner()

    help_result = runner.invoke(app, ["subagents", "--help"])
    run_help = runner.invoke(app, ["subagents", "run", "--help"])
    session_help = runner.invoke(app, ["subagents", "session", "--help"])

    assert help_result.exit_code == 0, help_result.output
    assert run_help.exit_code == 0, run_help.output
    assert session_help.exit_code == 0, session_help.output
    assert "profiles" in help_result.output
    assert "--goal" in unstyle(run_help.output)


def test_subagents_dry_run_session_created(tmp_path: Path) -> None:
    output_dir = tmp_path / "subagents"
    result = CliRunner().invoke(
        app,
        [
            "subagents",
            "run",
            "--skill",
            "improve_generated_candidates",
            "--project-id",
            "project-123",
            "--autonomy",
            "execute_with_approval",
            "--dry-run",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    session_path = Path(payload["session_path"])
    assert payload["status"] == "queued"
    assert payload["dry_run"] is True
    assert session_path.exists()
    session = json.loads(session_path.read_text())
    assert session["metadata"]["skill_name"] == "improve_generated_candidates"
    assert session["metadata"]["project_id"] == "project-123"
    assert session["metadata"]["autonomy"] == "execute_with_approval"
    assert session["tasks"]


def test_subagents_mocked_skill_executes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    output_dir = tmp_path / "subagents"

    def fake_execute(self: Any, **kwargs: Any) -> MultiAgentRuntimeExecution:
        del self
        session = expand_multi_agent_skill(
            "diagnose_project",
            user_goal=kwargs["user_goal"],
            parent_session_id="mock-session",
        )
        session.status = "succeeded"
        result = SubagentResult(
            result_id="mock-result",
            task_id=session.tasks[0].task_id,
            subagent_id=session.tasks[0].assigned_subagent_id,
            status="succeeded",
            output_json={
                "summary": "mocked execution",
                "findings": [],
                "recommended_next_actions": [],
            },
            output_text="mocked execution",
            artifact_ids=session.tasks[0].input_artifact_ids,
            tool_usage_ids=["mock-tool"],
            confidence=0.9,
            warnings=[],
            guardrail_findings=[],
            created_at=session.started_at,
            metadata={},
        )
        session.results = [result]
        return MultiAgentRuntimeExecution(
            session=session,
            runtime_sessions=[],
            results=[result],
            messages=session.messages,
            critiques=[],
            consensus=session.consensus[0],
            artifact_paths={},
        )

    monkeypatch.setattr(
        "molecule_ranker.subagents.executor.MultiAgentRuntimeExecutor.execute",
        fake_execute,
    )

    result = CliRunner().invoke(
        app,
        [
            "subagents",
            "run",
            "--goal",
            "Diagnose project readiness",
            "--skill",
            "diagnose_project",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    assert Path(payload["session_path"]).exists()
    assert json.loads((Path(payload["session_dir"]) / "subagent_results.json").read_text())[
        0
    ]["result_id"] == "mock-result"


def test_subagents_critique_command_works(tmp_path: Path) -> None:
    output_dir = tmp_path / "subagents"
    session = expand_multi_agent_skill(
        "diagnose_project",
        parent_session_id="critique-session",
    )
    result = SubagentResult(
        result_id="result-missing-schema",
        task_id=session.tasks[0].task_id,
        subagent_id=session.tasks[0].assigned_subagent_id,
        status="succeeded",
        output_json={"summary": "Only a summary."},
        output_text="Only a summary.",
        artifact_ids=session.tasks[0].input_artifact_ids,
        tool_usage_ids=[],
        confidence=0.8,
        warnings=[],
        guardrail_findings=[],
        created_at=session.started_at,
        metadata={},
    )
    session.results = [result]
    session_dir = output_dir / session.multi_agent_session_id
    session_dir.mkdir(parents=True)
    (session_dir / "multi_agent_session.json").write_text(
        json.dumps(session.model_dump(mode="json")),
        encoding="utf-8",
    )
    (session_dir / "subagent_results.json").write_text(
        json.dumps([result.model_dump(mode="json")]),
        encoding="utf-8",
    )

    response = CliRunner().invoke(
        app,
        [
            "subagents",
            "critique",
            "--result-id",
            "result-missing-schema",
            "--critic",
            "guardrail_sentinel",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.output)
    assert payload["result_id"] == "result-missing-schema"
    assert payload["critique_count"] >= 1
    assert (session_dir / "subagent_critiques.json").exists()


def test_subagents_consensus_command_works(tmp_path: Path) -> None:
    output_dir = tmp_path / "subagents"
    session = expand_multi_agent_skill(
        "diagnose_project",
        parent_session_id="consensus-session",
    )
    session.results = [
        SubagentResult(
            result_id="result-ok",
            task_id=session.tasks[0].task_id,
            subagent_id=session.tasks[0].assigned_subagent_id,
            status="succeeded",
            output_json={
                "summary": "ok",
                "findings": [],
                "recommended_next_actions": [],
            },
            output_text="ok",
            artifact_ids=session.tasks[0].input_artifact_ids,
            tool_usage_ids=[],
            confidence=0.8,
            warnings=[],
            guardrail_findings=[],
            created_at=session.started_at,
            metadata={},
        )
    ]
    session.consensus = []
    session_dir = output_dir / session.multi_agent_session_id
    session_dir.mkdir(parents=True)
    (session_dir / "multi_agent_session.json").write_text(
        json.dumps(session.model_dump(mode="json")),
        encoding="utf-8",
    )

    response = CliRunner().invoke(
        app,
        [
            "subagents",
            "consensus",
            "--session-id",
            "consensus-session",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.output)
    assert payload["session_id"] == "consensus-session"
    assert payload["consensus"]["consensus_status"] == "agreed"
    saved = MultiAgentSession.model_validate(
        json.loads((session_dir / "multi_agent_session.json").read_text())
    )
    assert isinstance(saved.consensus[0], SubagentConsensus)
