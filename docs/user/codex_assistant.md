# Codex Assistant

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## What Codex Does

Codex can summarize existing artifacts, explain candidate scores, suggest
project follow-up questions, and help with internal engineering tasks under
guardrails.

## What Codex Is Not Allowed To Do

Codex must not create biomedical evidence, invent citations, fabricate assay
results, access secret paths, read arbitrary files, provide clinical claims, or
turn generated molecules into validated findings.

## User Commands

```bash
molecule-ranker project summarize --use-codex --mode dry_run --json
molecule-ranker codex summarize-run results/example-disease-a --dry-run --json
```

## Interpreting Codex Output

Codex output is assistant text. It may help users understand scores,
developability, literature evidence, assay results, review items, active
learning, integrations, and dashboard state. It is not evidence and must remain
separate from source-backed records.
