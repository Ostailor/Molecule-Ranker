# molecule-ranker Pilot SDK

The pilot SDK provides a small synchronous client for hosted enterprise pilot APIs.
It authenticates with a service account token and preserves request IDs on API errors.

```python
from molecule_ranker.client import MoleculeRankerClient

client = MoleculeRankerClient(
    "https://molecule-ranker.internal",
    service_token="mrs_...",
)

project = client.create_project(workspace_id="pilot-demo", name="Pilot Demo")
projects = client.list_projects(limit=25, offset=0, sort="name")
job = client.submit_job(
    project_id=project["workspace_id"],
    job_type="ranking",
    config={"run_id": "pilot-demo-run"},
    idempotency_key="pilot-demo-run-ranking",
)
job = client.poll_job(job.job_id)
artifacts = client.list_artifacts(project_id=project["workspace_id"], limit=50)
```

Supported helpers:

- `authenticate(service_token)`
- `create_project(...)` and `list_projects(...)`
- `submit_job(...)`, `poll_job(...)`, and `list_jobs(...)`
- `list_artifacts(...)` and `download_artifact(...)`
- `submit_feedback(...)`
- `run_readiness()`
- `retrieve_evaluation_report(...)`

List helpers use `limit` and `offset` pagination. Project and job lists also accept
basic `filter` or `sort` parameters where supported by the API.

Errors are raised as `MoleculeRankerAPIError` subclasses. Each error includes
`status_code`, `error_code`, `request_id`, and `details` when the server provides
them.

```python
from molecule_ranker.client import AuthenticationError

try:
    client.list_projects()
except AuthenticationError as exc:
    print(exc.request_id)
```

Scientific boundaries are unchanged by the SDK. Generated molecules remain
computational hypotheses. Model predictions and evaluation reports are artifacts,
not assay results or biomedical evidence. Codex output is assistant output and is
not evidence, molecule data, scores, benchmark results, or decisions.

