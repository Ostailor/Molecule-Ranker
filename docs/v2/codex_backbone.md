# Codex Backbone

Codex is a guarded orchestration and summarization backbone. It is not a source
of biomedical truth and cannot create evidence, assay results, citations,
molecules, scores, graph records, review decisions, or campaign outcomes.

## Allowed Uses

- Summarize permitted artifacts.
- Draft workflow summaries and operational notes.
- Explain policy decisions and validation outputs.
- Assist with project navigation and support context.

## Prohibited Uses

Codex tasks cannot use raw assay files by default and cannot receive
unauthorized artifacts. Codex must not output medical advice, patient treatment
guidance, dosing guidance, synthesis instructions, lab protocols, fake evidence,
fake assay results, fake citations, generated molecule activity claims, docking
proof claims, model-prediction proof claims, or benchmark overclaims.

## Isolation and Redaction

Codex worker namespaces are scoped by tenant/project. Prompts must exclude
tokens, secrets, and unauthorized data. Transcripts are separate artifacts and
are excluded from support bundles by default.
