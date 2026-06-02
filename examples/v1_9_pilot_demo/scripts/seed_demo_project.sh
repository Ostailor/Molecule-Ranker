#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEMO_DIR}/../.." && pwd)"
DEMO_ROOT="${DEMO_ROOT:-${DEMO_DIR}/.demo_state}"

if [[ ! -f "${DEMO_ROOT}/demo.env" ]]; then
  "${SCRIPT_DIR}/bootstrap.sh"
fi

set -a
source "${DEMO_ROOT}/demo.env"
set +a

cd "${REPO_ROOT}"

DEMO_DIR="${DEMO_DIR}" \
DEMO_ROOT="${DEMO_ROOT}" \
DEMO_DB_PATH="${DEMO_DB_PATH}" \
DEMO_ADMIN_EMAIL="${DEMO_ADMIN_EMAIL}" \
uv run python - <<'PY'
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.platform.database import PlatformDatabase
from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore

project_id = "v1-9-pilot-demo"
run_id = "synthetic-run-001"
demo_dir = Path(os.environ["DEMO_DIR"])
root = Path(os.environ["DEMO_ROOT"])
db_path = Path(os.environ["DEMO_DB_PATH"])
admin_email = os.environ["DEMO_ADMIN_EMAIL"]
synthetic = demo_dir / "synthetic_data"
run_dir = root / "results" / run_id
artifact_dir = root / ".molecule-ranker" / "artifacts" / "pilot-demo"

run_dir.mkdir(parents=True, exist_ok=True)
artifact_dir.mkdir(parents=True, exist_ok=True)

shutil.copyfile(synthetic / "ranking_run.json", run_dir / "candidates.json")
shutil.copyfile(synthetic / "generated_candidates_v2.json", run_dir / "generated_candidates_v2.json")
shutil.copyfile(synthetic / "synthetic_experimental_import.csv", run_dir / "synthetic_experimental_import.csv")
shutil.copyfile(synthetic / "review_queue.json", run_dir / "review_queue.json")

(run_dir / "report.md").write_text(
    "# Synthetic V1.9 pilot demo report\n\n"
    "Synthetic fake data only. Generated molecules are computational hypotheses. "
    "No real biomedical claims, PMIDs, DOIs, protocols, synthesis instructions, dosing, "
    "or patient-treatment guidance are included.\n",
    encoding="utf-8",
)
(run_dir / "developability.json").write_text(
    json.dumps(
        {
            "summary": "Synthetic developability triage placeholder.",
            "not_evidence": True,
            "limitations": ["Demo artifact only."],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(run_dir / "experimental_results.json").write_text(
    json.dumps(
        {
            "import_id": "synthetic-experimental-import-001",
            "source_file": "synthetic_experimental_import.csv",
            "result_count": 2,
            "boundary": "Synthetic import demonstration only; not assay evidence.",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(run_dir / "design_plan.json").write_text(
    json.dumps(
        {
            "design_plan_id": "synthetic-design-plan-001",
            "disease_name": "Synthetic program A",
            "design_objectives": [
                {"objective_id": "synthetic-objective-001", "target_symbol": "SYN-TARGET-A"}
            ],
            "codex_task_result_id": "not-used-in-demo",
            "not_biomedical_evidence": True,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(run_dir / "oracle_scores.json").write_text(
    json.dumps(
        {
            "score_name": "synthetic_demo_priority_score",
            "candidate_count": 1,
            "claim_boundary": "computational triage only",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(run_dir / "experiment_readiness.json").write_text(
    json.dumps(
        {
            "candidates": [
                {
                    "molecule_id": "SYN-GEN-001",
                    "readiness_bucket": "ready_for_expert_review",
                    "blocking_risks": [],
                    "not_evidence": True,
                }
            ]
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
(run_dir / "benchmark_report.json").write_text(
    json.dumps(
        {
            "metrics": {"validity_rate": 1.0, "uniqueness_rate": 1.0},
            "boundary": "Synthetic benchmark artifact, not biomedical evidence.",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)

for name in (
    "source_manifest.json",
    "graph_summary.json",
    "hypothesis_summary.json",
    "campaign_summary.json",
    "evaluation_summary.json",
):
    shutil.copyfile(synthetic / name, artifact_dir / name)

store = ProjectWorkspaceStore(root)
workspace = store.create(workspace_id=project_id, name="V1.9 Pilot Demo")
workspace = store.register_run_dir(run_dir, run_id=run_id, workspace=workspace)
workspace = store.load()

artifact_records = {artifact.artifact_id: artifact for artifact in workspace.artifacts}
for path in sorted(artifact_dir.glob("*.json")):
    data = path.read_bytes()
    artifact_type = path.stem.replace("_summary", "") + "_summary"
    artifact_records[f"pilot-demo-{path.stem}"] = ArtifactRecord(
        artifact_id=f"pilot-demo-{path.stem}",
        workspace_id=project_id,
        path=str(path.resolve()),
        artifact_type=artifact_type,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        metadata={"synthetic_only": True, "not_biomedical_evidence": True},
    )
workspace.artifacts = sorted(artifact_records.values(), key=lambda item: item.artifact_id)

codex_output_dir = root / ".molecule-ranker" / "codex_project_outputs"
codex_output_dir.mkdir(parents=True, exist_ok=True)
codex_output = codex_output_dir / "synthetic-summary.json"
codex_output.write_text(
    json.dumps(
        {
            "task_type": "summarize_project",
            "workspace_id": project_id,
            "status": "succeeded",
            "output_text": (
                "Synthetic assistant summary grounded in synthetic artifacts. "
                "Assistant output is not evidence or a decision."
            ),
            "artifact_refs": ["pilot-demo-source_manifest"],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
workspace.codex_outputs = [
    {
        "task_type": "summarize_project",
        "status": "succeeded",
        "path": str(codex_output),
        "artifact_refs": ["pilot-demo-source_manifest"],
        "created_at": datetime.now(UTC).isoformat(),
    }
]
store.save(workspace)

database = PlatformDatabase(root, db_path=db_path)
admin = next(user for user in database.list_users() if user.email == admin_email)
database.grant_project_permission(
    project_id=project_id,
    role="owner",
    actor_user_id=admin.user_id,
    user_id=admin.user_id,
)

try:
    database.create_assignment(
        project_id=project_id,
        assigned_to_user_id=admin.user_id,
        assigned_by_user_id=admin.user_id,
        object_id="review-syn-001",
        run_id=run_id,
        candidate_id="Candidate Alpha",
        metadata={"synthetic_only": True},
    )
except Exception:
    pass

try:
    database.add_project_comment(
        project_id=project_id,
        author_user_id=admin.user_id,
        body="Synthetic pilot review note. Operational feedback only, not evidence.",
        object_type="run",
        object_id=run_id,
        run_id=run_id,
        metadata={"synthetic_only": True},
    )
except Exception:
    pass

queue = PlatformJobQueue(database)
existing_failed = [
    job
    for job in queue.list_jobs(project_id=project_id, limit=200)
    if job.job_type == "dashboard_build" and job.status == "failed"
]
if not existing_failed:
    job = queue.enqueue(
        job_type="dashboard_build",
        requested_by=admin,
        project_id=project_id,
        config_snapshot={"synthetic_demo": True},
    )
    queue.fail(job, RuntimeError("Synthetic demo worker unavailable. Safe remediation demo only."))

print(f"Seeded synthetic project: {project_id}")
print(f"Run directory: {run_dir}")
print(f"Artifact directory: {artifact_dir}")
PY
