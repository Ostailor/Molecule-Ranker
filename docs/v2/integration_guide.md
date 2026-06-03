# Integration Guide

V2.0 integrations connect internal research systems through governed,
auditable, tenant-scoped synchronization.

## Defaults

- External writes are disabled by default.
- Writes require admin approval, explicit write permission, and connector
  support.
- Integration credentials are scoped to org/project namespaces.
- Dry-run validation should be used before enabling writes.
- Integration sync outputs are artifacts and audit records, not scientific
  truth.

## Credential Handling

Never place integration tokens or credentials in docs, prompts, support
bundles, audit metadata, artifacts, or deployment images. Use secret manager or
orchestrator secret references.

## Data Boundaries

Do not sync patient treatment guidance, dosing content, lab protocols, synthesis
instructions, or unauthorized data. Imported assay records must be real
user-supplied data or synthetic demo data labeled as synthetic; do not fabricate
assay results.
