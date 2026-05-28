# Experimental Feedback

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Experimental Feedback Does

The experimental feedback workflow imports assay results from files, links them
to candidates or generated molecules, recalibrates scores when appropriate, and
creates active learning inputs.

## Importing Assay Results

Use file imports only:

```bash
molecule-ranker experiment import synthetic_assay_results.csv \
  --db-path experiments.sqlite \
  --dry-run \
  --json
```

Remove `--dry-run` only after checking the validation summary. Failed-QC rows
must not improve scores.

## Interpreting Imported Results

Imported assay results are user-supplied records with provenance. They are not
platform-generated truth. Check source file, source record ID, QC status,
candidate identity, target, and linkage metadata.

## Relationship To Review

Assay results may inform ranking scores, review workflow comments, active
learning batches, integrations records, Codex summaries, and dashboard views.
They must remain separate from review decisions.
