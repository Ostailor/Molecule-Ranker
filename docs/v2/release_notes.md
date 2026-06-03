# V2.1 Release Notes

V2.1 upgrades the Codex CLI integration from guarded summarization and hosted
worker jobs into a controlled runtime agent backbone for approved,
audited, deterministic molecule-ranker tools. It preserves the V2.0 validated
enterprise discovery operating system and its internal-research guardrails.

## Runtime Agent Additions

- `CodexRuntimeAgent` for objective-to-action workflow execution.
- Deterministic `ActionPlanner` that converts research objectives into
  reviewable molecule-ranker action plans.
- Controlled `ToolRegistry` for approved actions and canonical permissions.
- `PolicyEngine` and `ApprovalGate` checks before any tool executes.
- `ActionExecutor` with bounded retry recovery for registered recoverable tool
  failures.
- `ArtifactValidator` for reviewable output artifact references.
- `GuardrailChecker` for prohibited biomedical claims, fabricated records,
  direct score changes, stage/campaign approval, RBAC/policy bypasses, medical
  advice, protocols, synthesis instructions, and dosing guidance.
- `AuditLogger` JSONL-compatible audit events across planning, registry,
  policy, approval, execution, validation, guardrails, recovery, and completion.

## V2.0 Enterprise-Stable Baseline

V2.0 shipped molecule-ranker as a validated enterprise discovery operating
system for internal research teams:

- Stable V2 release contracts and compatibility matrix.
- `/api/v2` API surface and stable SDK v2.
- Enterprise identity hardening, OIDC readiness, sessions, and scoped service
  accounts.
- Tenant/project isolation checks and isolation audit.
- Policy engine for scientific, Codex, integration, export, retention, review,
  and campaign controls.
- Validation evidence package generation.
- Golden enterprise workflow with synthetic mocked data.
- Disaster recovery drill.
- SLO reporting and observability dashboard.
- Deployment packaging for Docker Compose, Kubernetes, Helm-like templates, and
  offline/local guidance.
- Enterprise admin console.
- V2 documentation and training material.

## Non-Goals

V2.1 does not add major new science modules and does not expand generation,
docking, ADMET, graph reasoning, model training, integrations, or campaign
planning except by allowing approved deterministic tools to be orchestrated
through the controlled Codex runtime path.

## Boundaries

V2.1 is for internal research use only. It does not provide medical advice,
synthesis instructions, lab protocols, dosing, or patient treatment guidance.
It does not claim molecules are safe, active, effective, binding, or
synthesizable. Codex is orchestration/summarization, not scientific truth.
