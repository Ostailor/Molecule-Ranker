# Integrations

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Integrations Do

Integrations connect molecule-ranker to existing systems through approved
connectors, dry-run syncs, mapping review, warehouse exports, file imports, and
webhooks.

## Safe Use

Start with dry-run mode:

```bash
molecule-ranker integration sync run \
  --external-system-id ext-example \
  --direction import \
  --object-type assay_results \
  --project-id project-example \
  --dry-run \
  --json
```

Review mappings before write-enabled operations. Confirm credential references
are redacted and stored through approved configuration.

## Interpreting Integration Data

External records are provenance and synchronization records. They do not create
clinical claims or validate generated molecules. Check source IDs, mapping
status, audit logs, and data contracts.

## Relationship To Platform Views

Integration metadata can appear in ranking scores, assay results linkage,
review workflow, active learning, Codex summaries, and dashboards. Keep unsafe
or unreviewed mappings out of evidence summaries.
