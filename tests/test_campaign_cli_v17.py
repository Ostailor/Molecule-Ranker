from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def test_campaign_cli_help_works() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["campaign", "--help"])

    assert result.exit_code == 0
    assert "create" in result.output
    assert "replan" in result.output


def test_campaign_create_and_plan_with_synthetic_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    paths = _write_synthetic_campaign_artifacts(tmp_path)
    campaign_path = tmp_path / "campaign.json"
    plan_path = tmp_path / "campaign_plan.json"
    runner = CliRunner()

    create = runner.invoke(
        app,
        [
            "campaign",
            "create",
            "--project-id",
            "project-1",
            "--program-id",
            "program-1",
            "--name",
            "Synthetic campaign",
            "--description",
            "High-level campaign planning artifact.",
            "--from-hypotheses",
            str(paths["hypotheses"]),
            "--from-portfolio",
            str(paths["portfolio"]),
            "--output",
            str(campaign_path),
        ],
    )
    plan = runner.invoke(
        app,
        [
            "campaign",
            "plan",
            "--campaign",
            str(campaign_path),
            "--budget-assay-slots",
            "0",
            "--budget-review-hours",
            "3",
            "--budget-compute-units",
            "2",
            "--strategy",
            "balanced",
            "--output",
            str(plan_path),
        ],
    )

    assert create.exit_code == 0, create.output
    assert plan.exit_code == 0, plan.output
    created = json.loads(campaign_path.read_text(encoding="utf-8"))
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert created["campaign"]["campaign_id"]
    assert payload["campaign_id"] == created["campaign"]["campaign_id"]
    assert payload["budget_summary"]["usage"]["assay_slots"] == 0
    assert payload["metadata"]["excluded_work_package_ids"]


def test_campaign_approve_gate_and_update_status_write_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    campaign_path, plan_path = _create_and_plan(tmp_path)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    gate_id = payload["stage_gates"][0]["gate_id"]
    work_package_id = payload["work_packages"][0]["work_package_id"]
    runner = CliRunner()

    approve = runner.invoke(
        app,
        [
            "campaign",
            "approve",
            "--campaign-id",
            payload["campaign_id"],
            "--stage-gate-id",
            gate_id,
            "--reviewer-id",
            "reviewer-1",
            "--rationale",
            "Reviewed deterministic campaign artifacts.",
        ],
    )
    update = runner.invoke(
        app,
        [
            "campaign",
            "update-work-package",
            "--work-package-id",
            work_package_id,
            "--status",
            "ready",
            "--actor",
            "reviewer-1",
        ],
    )
    status = runner.invoke(app, ["campaign", "status", "--campaign-id", payload["campaign_id"]])

    assert campaign_path.exists()
    assert approve.exit_code == 0, approve.output
    assert update.exit_code == 0, update.output
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    event_types = [event["event_type"] for event in status_payload["events"]]
    assert "stage_gate_decided" in event_types
    assert "approved" in event_types


def test_campaign_replan_from_synthetic_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _, plan_path = _create_and_plan(tmp_path)
    campaign_id = json.loads(plan_path.read_text(encoding="utf-8"))["campaign_id"]
    event_path = tmp_path / "event.json"
    output = tmp_path / "updated_campaign_plan.json"
    event_path.write_text(
        json.dumps(
            {
                "event_type": "result_imported",
                "result_interpretation": "positive",
                "hypothesis_id": "hypothesis-1",
                "linked_entity_ids": ["hypothesis-1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "campaign",
            "replan",
            "--campaign-id",
            campaign_id,
            "--event-artifact",
            str(event_path),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "new_positive_result" in payload["replan_triggers"]


def test_campaign_memo_has_no_protocol_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _, plan_path = _create_and_plan(tmp_path)
    memo_path = tmp_path / "campaign_memo.md"

    result = CliRunner().invoke(
        app,
        [
            "campaign",
            "memo",
            "--campaign-plan",
            str(plan_path),
            "--output",
            str(memo_path),
            "--use-codex",
        ],
    )

    assert result.exit_code == 0, result.output
    memo = memo_path.read_text(encoding="utf-8").lower()
    assert "campaign memo" in memo
    assert not any(
        term in memo
        for term in ["protocol", "reagent", "concentration", "incubat", "mg/kg", "temperature"]
    )


def test_campaign_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _, plan_path = _create_and_plan(tmp_path)
    campaign_id = json.loads(plan_path.read_text(encoding="utf-8"))["campaign_id"]
    output = tmp_path / "campaign_export.json"

    result = CliRunner().invoke(
        app,
        ["campaign", "export", "--campaign-id", campaign_id, "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["campaign"]["campaign_id"] == campaign_id
    assert payload["campaign_plans"]


def _create_and_plan(tmp_path: Path) -> tuple[Path, Path]:
    paths = _write_synthetic_campaign_artifacts(tmp_path)
    campaign_path = tmp_path / "campaign.json"
    plan_path = tmp_path / "campaign_plan.json"
    runner = CliRunner()
    create = runner.invoke(
        app,
        [
            "campaign",
            "create",
            "--project-id",
            "project-1",
            "--program-id",
            "program-1",
            "--name",
            "Synthetic campaign",
            "--from-hypotheses",
            str(paths["hypotheses"]),
            "--from-portfolio",
            str(paths["portfolio"]),
            "--output",
            str(campaign_path),
        ],
    )
    assert create.exit_code == 0, create.output
    plan = runner.invoke(
        app,
        [
            "campaign",
            "plan",
            "--campaign",
            str(campaign_path),
            "--budget-assay-slots",
            "1",
            "--budget-review-hours",
            "4",
            "--budget-compute-units",
            "2",
            "--strategy",
            "balanced",
            "--output",
            str(plan_path),
        ],
    )
    assert plan.exit_code == 0, plan.output
    return campaign_path, plan_path


def _write_synthetic_campaign_artifacts(tmp_path: Path) -> dict[str, Path]:
    hypotheses = tmp_path / "hypotheses.json"
    portfolio = tmp_path / "portfolio_optimization.json"
    hypotheses.write_text(
        json.dumps(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "hypothesis-1",
                        "hypothesis_type": "generated_molecule",
                        "title": "Generated planning hypothesis",
                        "statement": "Generated molecule remains a computational hypothesis.",
                        "priority_score": 0.9,
                        "uncertainty_score": 0.8,
                        "metadata": {
                            "candidate_ids": ["candidate-1"],
                            "generated_molecule": True,
                        },
                    },
                    {
                        "hypothesis_id": "hypothesis-2",
                        "hypothesis_type": "mechanism",
                        "title": "Assay triage planning hypothesis",
                        "statement": "High-level campaign planning hypothesis.",
                        "priority_score": 0.7,
                        "metadata": {
                            "candidate_ids": ["candidate-2"],
                            "requires_assay_triage": True,
                        },
                    },
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    portfolio.write_text(
        json.dumps(
            {
                "selections": [
                    {
                        "selection_id": "selection-1",
                        "selected_candidate_ids": ["candidate-1", "candidate-2"],
                    }
                ],
                "selected_candidate_ids": ["candidate-1", "candidate-2"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {"hypotheses": hypotheses, "portfolio": portfolio}
