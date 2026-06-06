from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.v3.discover import V3DiscoverRequest, run_v3_discover
from molecule_ranker.v3.governance_matrix import (
    build_v3_human_governance_matrix,
    validate_v3_governance_decision,
    write_v3_human_governance_matrix,
)


def test_generated_advancement_approval_required() -> None:
    matrix = build_v3_human_governance_matrix()

    requirement = matrix.requirement("generated_molecule_advancement")

    assert requirement.human_required is True
    assert requirement.requirement_type == "approval"
    assert requirement.codex_self_approval_allowed is False
    assert matrix.requires_human_approval("generated_antibody_advancement") is True


def test_external_write_approval_required() -> None:
    matrix = build_v3_human_governance_matrix()

    requirement = matrix.requirement("external_write")

    assert requirement.human_required is True
    assert requirement.requirement_type == "approval"
    assert requirement.codex_self_approval_allowed is False
    assert matrix.requires_human_approval("write_approved_live_workflow") is True


def test_codex_self_approval_blocked() -> None:
    decision = validate_v3_governance_decision(
        action_id="stage_gate_approval",
        actor_type="codex",
        approval_ids=["codex-self-approved"],
    )

    assert decision.valid is False
    assert decision.human_approval_required is True
    assert "Codex self-approval is blocked" in decision.issues


def test_governance_matrix_writes_json_and_markdown(tmp_path: Path) -> None:
    artifacts = write_v3_human_governance_matrix(
        build_v3_human_governance_matrix(),
        output_dir=tmp_path,
    )

    assert artifacts["v3_human_governance_matrix.json"] == str(
        tmp_path / "v3_human_governance_matrix.json"
    )
    assert artifacts["v3_human_governance_matrix.md"] == str(
        tmp_path / "v3_human_governance_matrix.md"
    )
    payload = json.loads((tmp_path / "v3_human_governance_matrix.json").read_text())
    markdown = (tmp_path / "v3_human_governance_matrix.md").read_text()
    assert "external_write" in payload["approval_required"]
    assert "generated hypotheses" in markdown


def test_matrix_included_in_result_bundle(tmp_path: Path) -> None:
    result = run_v3_discover(
        V3DiscoverRequest(
            disease="Parkinson disease",
            mode="mocked",
            output_dir=tmp_path,
        )
    )

    bundle_payload = json.loads(
        Path(result.artifacts["v3_result_bundle.json"]).read_text(encoding="utf-8")
    )
    filenames = {item["filename"] for item in bundle_payload["artifact_manifest"]}

    assert "v3_human_governance_matrix.json" in filenames
    assert "v3_human_governance_matrix.md" in filenames
    assert (tmp_path / "v3_human_governance_matrix.json").exists()
    assert (tmp_path / "v3_human_governance_matrix.md").exists()
