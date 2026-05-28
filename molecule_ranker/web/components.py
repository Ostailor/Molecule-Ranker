from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from molecule_ranker.platform.database import teams
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import filter_visible_projects
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.workspace.schemas import ProjectRun, ProjectWorkspace
from molecule_ranker.workspace.store import ProjectWorkspaceStore

DASHBOARD_TEXT_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:clinically\s+)?validated\s+actives?\b", re.I),
        "[unsupported validation claim redacted]",
    ),
    (
        re.compile(r"\b(?:synthesis route|retrosynthesis|synthesize(?:d|s|ing)?)\b", re.I),
        "[operational chemistry text redacted]",
    ),
    (
        re.compile(r"\b(?:lab protocols?|step[- ]by[- ]step protocol)\b", re.I),
        "[lab protocol text redacted]",
    ),
    (
        re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg per kg|mg/day|mg daily)\b", re.I),
        "[dosing text redacted]",
    ),
    (
        re.compile(r"\b(?:dose|dosing)\b.{0,80}\b(?:patient|human|animal|mg/kg|mg/day)\b", re.I),
        "[dosing text redacted]",
    ),
)


@dataclass(frozen=True)
class DashboardRun:
    run: ProjectRun
    payload: dict[str, Any]

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return _records(self.payload.get("candidates"))

    @property
    def generated_molecules(self) -> list[dict[str, Any]]:
        generated = _records(self.payload.get("generated_molecule_hypotheses"))
        generated_artifact = self.design_generation
        generated.extend(_records(generated_artifact.get("retained_generated_molecules")))
        generated.extend(_records(generated_artifact.get("generated_molecules")))
        return _dedupe_records(generated, keys=("generated_id", "name", "canonical_smiles"))

    @property
    def experimental_results(self) -> list[dict[str, Any]]:
        direct = _records(self.payload.get("assay_results"))
        evidence = self.payload.get("experimental_evidence")
        if isinstance(evidence, dict):
            direct.extend(_records(evidence.get("assay_results")))
            direct.extend(_records(evidence.get("results")))
        direct.extend(_records(self.payload.get("experimental_results")))
        return direct

    @property
    def active_learning(self) -> dict[str, Any]:
        value = self.payload.get("active_learning")
        return value if isinstance(value, dict) else {}

    @property
    def design_plan(self) -> dict[str, Any]:
        return _load_run_json(self.run, "design_plan.json")

    @property
    def design_generation(self) -> dict[str, Any]:
        return _load_first_run_json(
            self.run,
            ("generated_candidates_v2.json", "generated_candidates.json"),
        )

    @property
    def oracle_scores(self) -> dict[str, Any]:
        return _load_run_json(self.run, "oracle_scores.json")

    @property
    def experiment_readiness(self) -> dict[str, Any]:
        return _load_run_json(self.run, "experiment_readiness.json")

    @property
    def design_benchmark(self) -> dict[str, Any]:
        return _load_run_json(self.run, "benchmark_report.json")

    @property
    def design_loop_report_path(self) -> str | None:
        path = Path(self.run.run_dir) / "design_loop_report.md"
        if not path.exists() or not _path_is_allowed(path):
            return None
        return str(path)


def visible_workspaces(
    *,
    store: ProjectWorkspaceStore,
    database: PlatformDatabase,
    user: UserAccount,
) -> list[ProjectWorkspace]:
    if not store.workspace_path.exists():
        return []
    workspace = store.load()
    return filter_visible_projects(
        database,
        user,
        [workspace],
        id_getter=lambda item: item.workspace_id,
    )


def load_project(
    *,
    store: ProjectWorkspaceStore,
    project_id: str,
) -> ProjectWorkspace | None:
    if not store.workspace_path.exists():
        return None
    workspace = store.load()
    if workspace.workspace_id != project_id:
        return None
    return workspace


def load_dashboard_run(workspace: ProjectWorkspace, run_id: str) -> DashboardRun | None:
    run = next((item for item in workspace.runs if item.run_id == run_id), None)
    if run is None:
        return None
    return DashboardRun(run=run, payload=load_run_payload(run))


def load_run_payload(run: ProjectRun) -> dict[str, Any]:
    run_dir = Path(run.run_dir)
    candidates_path = run_dir / "candidates.json"
    if not candidates_path.exists() or not _path_is_allowed(candidates_path):
        return {}
    try:
        payload = json.loads(candidates_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def dashboard_design_runs(workspace: ProjectWorkspace) -> list[DashboardRun]:
    runs: list[DashboardRun] = []
    for run in workspace.runs:
        dashboard_run = load_dashboard_run(workspace, run.run_id)
        if dashboard_run is None:
            continue
        if any(
            [
                dashboard_run.design_plan,
                dashboard_run.design_generation,
                dashboard_run.oracle_scores,
                dashboard_run.experiment_readiness,
                dashboard_run.design_benchmark,
                dashboard_run.design_loop_report_path,
            ]
        ):
            runs.append(dashboard_run)
    return runs


def candidate_by_name(dashboard_run: DashboardRun, candidate_name: str) -> dict[str, Any] | None:
    all_candidates = [*dashboard_run.candidates, *dashboard_run.generated_molecules]
    for candidate in all_candidates:
        if str(candidate.get("name") or candidate.get("candidate_name") or "") == candidate_name:
            return candidate
    return None


def codex_outputs(workspace: ProjectWorkspace) -> list[dict[str, Any]]:
    safe_outputs: list[dict[str, Any]] = []
    root = Path(workspace.root_dir).resolve()
    for output in workspace.codex_outputs:
        if not isinstance(output, dict):
            continue
        safe_record = {
            "task_type": output.get("task_type"),
            "status": output.get("status"),
            "created_at": output.get("created_at"),
            "artifact_refs": output.get("artifact_refs") or [],
            "summary": "",
        }
        output_path = output.get("path")
        if isinstance(output_path, str):
            path = Path(output_path)
            if _path_is_allowed(path) and _under_root(path, root):
                try:
                    payload = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    safe_record["summary"] = _codex_output_summary(payload)
        safe_record["summary"] = safe_dashboard_text(str(safe_record["summary"]))
        safe_outputs.append(safe_record)
    return safe_outputs


def list_admin_teams(database: PlatformDatabase) -> list[dict[str, Any]]:
    with database.engine.connect() as connection:
        rows = connection.execute(select(teams).order_by(teams.c.name)).mappings().fetchall()
    return [
        {
            "team_id": str(row["team_id"]),
            "org_id": str(row["org_id"]),
            "name": str(row["name"]),
            "slug": str(row["slug"]),
        }
        for row in rows
    ]


def prediction_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "score",
        "score_breakdown",
        "confidence",
        "model_score",
        "predicted_activity",
        "developability_score",
        "developability_summary",
        "developability_assessment",
    ]
    return {key: candidate[key] for key in keys if key in candidate}


def evidence_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "evidence",
        "evidence_summary",
        "literature_evidence",
        "source_citations",
        "known_targets",
        "experimental_results",
    ]
    return {key: candidate[key] for key in keys if key in candidate}


def display_candidate_name(candidate: dict[str, Any]) -> str:
    return str(candidate.get("name") or candidate.get("candidate_name") or "unknown")


def safe_dashboard_text(value: Any) -> str:
    text = str(value)
    for pattern, replacement in DASHBOARD_TEXT_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _codex_output_summary(payload: dict[str, Any]) -> str:
    summary = payload.get("output_text") or payload.get("summary") or ""
    if isinstance(summary, str):
        try:
            parsed = json.loads(summary)
        except json.JSONDecodeError:
            return summary
        if isinstance(parsed, dict) and parsed.get("dry_run") is True:
            return (
                "Dry-run Codex request prepared; no live Codex execution. "
                "The full prompt is stored in the Codex artifact and is not displayed here."
            )
    return str(summary)


def candidate_comment_key(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("candidate_id")
        or candidate.get("id")
        or candidate.get("name")
        or candidate.get("candidate_name")
        or "unknown"
    )


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe_records(
    records: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        identity = next((str(record.get(key)) for key in keys if record.get(key)), "")
        if not identity:
            identity = str(len(deduped))
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(record)
    return deduped


def _load_first_run_json(run: ProjectRun, names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        payload = _load_run_json(run, name)
        if payload:
            return payload
    return {}


def _load_run_json(run: ProjectRun, name: str) -> dict[str, Any]:
    path = Path(run.run_dir) / name
    if not path.exists() or not _path_is_allowed(path):
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _path_is_allowed(path: Path) -> bool:
    lowered = str(path).lower()
    blocked = (".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")
    return not any(marker in lowered for marker in blocked)


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True
