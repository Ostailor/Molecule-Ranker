# Architecture

V2.0 is organized as an enterprise platform around existing scientific
workflows. The release focus is stability, validation, deployment, security,
identity, isolation, observability, and governance. It does not introduce major
new science modules.

## Main Components

- API server exposing stable `/api/v2` routes and compatibility routes where
  supported.
- Platform database for users, roles, orgs, teams, projects, jobs, sessions,
  service accounts, audit records, and operational metadata.
- Artifact store with hashed, scoped project artifacts.
- Worker queues for ranking, generation, developability, model, structure,
  portfolio, graph, hypothesis, campaign, evaluation, integration, and Codex
  jobs.
- Admin console for enterprise administration, audit, policies, backup/restore,
  support bundles, SLOs, and validation packages.
- SDK v2 for typed `/api/v2` automation.
- Validation package builder and release gate.

## Trust Boundaries

Every API query is scoped by authenticated user permissions. Artifact, job,
integration, Codex worker, model registry, graph, and project namespaces are
tenant/project scoped. Cross-project views require permission across every
included project.

Codex never creates evidence, assay results, molecules, scores, decisions,
citations, graph records, or biomedical truth. Codex outputs are separate
artifacts that require review.

## Scientific Boundaries

Generated molecules are computational hypotheses. Model predictions are
prioritization signals. Docking outputs are structure-workflow artifacts.
Benchmark and evaluation outputs measure software/research workflow behavior.
None of these are medical advice, clinical validation, lab instructions,
synthesis plans, dosing guidance, or patient treatment guidance.
