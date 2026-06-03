# Integration Admin Training

Audience: administrators configuring external research-system integrations.

## Interpretation Boundaries

Integration sync moves governed data between systems. It does not create
scientific truth, assay results, clinical conclusions, lab protocols, synthesis
instructions, dosing, or patient treatment guidance.

## Checklist

- Use dry-run mode first.
- Scope credentials by org/project.
- Disable writes by default.
- Require admin approval for external writes.
- Store credentials only in approved secret managers.
- Confirm integration audit events.
- Validate support bundles redact integration metadata.

## Exercise: Synthetic Connector Dry Run

Synthetic data:

- External system: `demo-eln`
- Project: `demo-project-alpha`
- Connector mode: `dry_run`
- Record: `synthetic-record-001`

Steps:

1. Register the external system with dry-run mode.
2. Add a credential reference, not a credential value.
3. Run sync preview with `synthetic-record-001`.
4. Confirm no external write occurred.
5. Review audit log and sync report.

Expected outcomes:

- Connector remains in dry-run mode.
- Credential value is never displayed.
- Sync report is scoped to the project.
- Audit log records preview action.

## Common Mistakes

- Enabling writes before dry-run validation.
- Reusing credentials across tenants.
- Putting token values in support tickets.
- Treating imported synthetic records as real assay results.
