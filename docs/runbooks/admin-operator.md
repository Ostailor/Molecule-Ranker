# Admin and Operator Runbook

This runbook is for V1.5 internal hosted deployments. molecule-ranker is not a
regulated clinical product and does not provide medical advice, dosage,
synthesis instructions, lab protocols, or patient treatment guidance.

## Startup

1. Apply database migrations:

   ```bash
   molecule-ranker db migrate --database-url "$MOLECULE_RANKER_DATABASE_URL"
   ```

2. Start the web process and worker:

   ```bash
   molecule-ranker serve --hosted --auth-secret "$MOLECULE_RANKER_AUTH_SECRET"
   molecule-ranker worker run
   ```

3. Verify:

   ```bash
   curl -fsS http://127.0.0.1:8765/health
   curl -fsS http://127.0.0.1:8765/ready
   curl -fsS http://127.0.0.1:8765/version
   ```

The version endpoint must report `1.7.0`, `api.v1`, `artifacts.v1`,
`data-contracts.v1`, and `mr_warehouse_v1.0.0`.

## Routine Operations

- Review `/metrics` for job failures, auth failures, Codex guardrail failures,
  and request latency.
- Review audit logs after user, permission, integration, export, deletion,
  retention, and Codex job actions.
- Keep connector modes read-only, dry-run, or sandbox unless write/export
  permission is explicitly approved.
- Keep Codex worker credentials outside project artifact paths and outside the
  repository.

## Incident Triage

- Auth/RBAC issue: disable affected service tokens, inspect audit events, and
  verify project permissions before re-enabling.
- Codex guardrail issue: stop the worker, retain redacted transcripts, inspect
  the job artifact context, and re-run guardrail tests before restarting.
- Integration issue: pause the connector, preserve sync job records, validate
  data contracts, and reject unconfirmed Codex mapping suggestions.
- Data provenance issue: quarantine the affected project export, compare
  artifact hashes against manifests, and rerun only source-backed retrieval.

## Backup and Restore

Backup cadence and restore drills are release gates for V1.5.

Backup:

- Dump PostgreSQL with the organization-approved tool, or stop writers before
  copying SQLite.
- Back up `/data/artifacts`, `/data/projects`, and `/data/storage` as one
  consistency group.
- Exclude caches, `.env` files, API keys, service tokens, Codex credentials,
  secret-manager mounts, and temporary worker scratch directories.

Restore check:

1. Restore the database and storage paths into an isolated environment.
2. Run `molecule-ranker db migrate` and `molecule-ranker db check`.
3. Verify `/ready` and `/version`.
4. Export one synthetic project package and verify manifest hashes.
5. Record restore duration, data-loss window, artifact hash sample, and operator.

## Rollback

Rollback is allowed only to a build with the same data contract family or a
documented migration path. Before rollback, preserve the current database dump,
artifact storage snapshot, worker logs, and release package manifest.
