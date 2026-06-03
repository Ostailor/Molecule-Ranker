# Data Governance

V2.0 governance is built around provenance, contracts, audit logs, retention,
delete controls, artifact hashing, tenant/project isolation, and redaction.

## Artifact Governance

- V2 artifacts include `schema_version` and `contract_version`.
- Artifacts are hashed and project-scoped.
- Downloads validate tenant/project access.
- Exports exclude secrets, cache, and unauthorized artifacts.
- Codex outputs, review decisions, evaluation outputs, and campaign plans remain
  separate artifact classes.

## Retention and Delete

Retention policies define what is kept, deleted, exported, and audited. Delete
operations must respect tenant/project scope and preserve required audit
metadata without storing secrets.

## Provenance

Source-backed ranking depends on explicit provenance. Graph inferences,
generated hypotheses, model predictions, docking outputs, and Codex summaries
must not be converted into evidence unless backed by proper source or imported
result records.
