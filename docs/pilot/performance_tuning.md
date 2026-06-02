# Performance Tuning

## Baseline

Use synthetic or mocked workflows by default. Do not call live external APIs for
pilot baseline profiling.

```bash
uv run molecule-ranker performance profile --workflow golden
uv run molecule-ranker performance report --from-profile performance_report.json
```

## Monitor

```bash
uv run molecule-ranker ops metrics --root "$PILOT_ROOT" --db-path "$PILOT_DB"
uv run molecule-ranker ops alerts --root "$PILOT_ROOT" --db-path "$PILOT_DB"
```

Track:

- Dashboard response time.
- API response time.
- Job queue wait time.
- Job run time.
- Artifact read and write time.
- Memory usage by workflow step.
- Codex task duration and timeout rate if Codex worker is enabled.

## Common Tuning Actions

- Use pagination for dashboard and API list pages.
- Keep large artifact reads lazy where possible.
- Prefer summary caches for repeated dashboard views.
- Use bounded-memory modes for large artifacts.
- Batch feature computation where available.
- Keep queue polling intervals bounded.
- Archive stale artifacts according to retention policy.

## When To Escalate

Escalate if:

- Queue backlog remains high after worker recovery.
- Latency percentiles exceed internal targets for repeated intervals.
- Memory warnings recur.
- Artifact storage reports repeated read or write errors.
- Dashboard pages degrade after adding a large project.

