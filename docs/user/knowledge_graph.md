# Knowledge Graph

The V1.5 knowledge graph is a cross-program memory and reasoning layer over
existing molecule-ranker artifacts. It helps teams compare recurring mechanisms,
target outcomes, scaffold and chemical-series patterns, assay contradictions,
developability blockers, literature and experimental disagreements, expert
review decisions, novelty checks, and stale or unsupported hypotheses.

The graph is research use only. It provides no medical advice, no clinical
claims, no lab protocols, no synthesis instructions, and no dosing. Generated
molecules require validation.

## What The Graph Does

- Builds `KnowledgeGraph`, `GraphEntity`, and `GraphRelation` records from
  existing ranking, literature, generated-molecule, assay result, developability,
  review, and portfolio artifacts.
- Normalizes identifiers and aliases for diseases, targets, molecules,
  mechanisms, assays, literature claims, scaffolds, series, risks, and expert
  decisions.
- Preserves provenance for every entity and relation.
- Marks graph-inferred relations as hypotheses unless they are backed by source
  evidence.
- Flags unsupported, stale, contradicted, and ready-for-review hypotheses.
- Renders a graph dashboard for cross-program review.
- Provides a Codex graph assistant that can summarize graph-backed patterns but
  cannot create graph records or scientific records.

## Boundaries

Graph paths do not prove causality, efficacy, safety, binding, activity, or
synthesizability. Graph inference must not create `EvidenceItem` records, assay
results, citations, mechanisms, molecules, nodes, or edges. Codex graph output is
assistant text only and must remain separate from evidence, assay results,
review decisions, and score updates.

Use graph recommendations as review prompts. Before reusing prior program
knowledge, inspect the source artifacts and confirm whether the relation is
source-backed, contradicted, stale, or graph-inferred.
