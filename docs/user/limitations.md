# Limitations

## Safety Notice

molecule-ranker is for research use only. It provides no medical advice, no
clinical claims, no lab protocols, no synthesis instructions, and no dosing.
Generated molecules require validation before they can be treated as anything
more than computational hypotheses.

## Platform Limits

molecule-ranker is an internal research triage platform. It depends on source
coverage, retrieval quality, configured scoring, file-import provenance,
reviewer judgment, active learning strategy, integration contracts, Codex
guardrails, and dashboard presentation.

## Score Limits

Scores are not probabilities of success. They rank candidates for review.
Changes in source data, developability configuration, assay results, or
integration mappings can change scores.

## Evidence Limits

Literature evidence can be incomplete or contradictory. Assay results must be
imported from files and checked for QC and identity. Generated molecules do not
have direct evidence unless exact imported results exist.

## Structure Limits

Structure workflows are optional computational triage. Docking scores are not
proof of binding, poses are not experimental evidence, structure-based scores
are not activity evidence, and predicted structures are lower-confidence than
suitable experimental structures. Codex can plan and summarize structure
workflows, but it cannot invent structures, poses, binding sites, docking
scores, or interactions.

## Codex And Dashboard Limits

Codex can summarize artifacts but cannot create evidence. Dashboards can make
navigation easier but should not replace artifact inspection, expert review, or
release guardrail checks.
