# Operator Training

Audience: platform operators responsible for deployment, observability,
backup/restore, disaster recovery, support bundles, and release gates.

## Interpretation Boundaries

Operator workflows validate platform availability, recoverability, security,
and process evidence. They do not validate clinical use, molecule activity,
safety, efficacy, binding, synthesizability, dosing, or patient treatment.

## Checklist

- Check `/health`, `/ready`, `/api/v2/version`, and admin health.
- Review job queue status and worker health.
- Generate SLO report and review error budget.
- Confirm backup freshness.
- Run disaster recovery drill.
- Generate redacted support bundle.
- Run release gate before promotion.

## Exercise: Synthetic DR Drill

Synthetic data:

- Project: `demo-project-alpha`
- Artifact: `synthetic_rankings.json`
- Backup target: `dr-demo-backup`
- Restored environment: `dr-demo-restore`

Steps:

1. Create a backup.
2. Verify backup manifest.
3. Restore into the temporary environment.
4. Validate artifact hashes and user/role metadata.
5. Run smoke workflow on the restored environment.
6. Produce DR report.

Expected outcomes:

- Backup manifest is valid.
- Restored project and artifact load.
- No secrets are included in backup artifacts.
- Smoke workflow passes.
- DR report is available for validation package inclusion.

## Common Mistakes

- Treating backup creation as sufficient without restore verification.
- Including cache or secrets in backup artifacts.
- Ignoring stale backup SLO failures.
- Promoting a release without a release gate report.
