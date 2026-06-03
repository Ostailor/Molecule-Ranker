# V2.1 Codex Runtime Agent

V2.1 makes Codex CLI the runtime LLM agent backbone for molecule-ranker tasks
only through approved, audited, deterministic tools. Codex may understand a
research objective and propose a safe plan, but execution is controlled by the
runtime architecture:

`CodexRuntimeAgent -> ActionPlanner -> ToolRegistry -> PolicyEngine -> ApprovalGate -> ActionExecutor -> ArtifactValidator -> GuardrailChecker -> AuditLogger`

## Runtime Contract

The runtime agent may execute registered molecule-ranker actions such as:

- `create_project`
- `run_ranking`
- `run_generation`
- `run_developability`
- `run_literature_update`
- `run_model_prediction`
- `run_structure_assessment`
- `build_graph`
- `generate_hypotheses`
- `optimize_portfolio`
- `plan_campaign`
- `run_evaluation`
- `create_review_workspace`
- `export_reports`
- `inspect_failed_jobs`
- `generate_support_bundle`

Each action must be present in the controlled `ToolRegistry`, require its
canonical permission, pass RBAC and policy checks, satisfy approval gates when
the registered tool marks approval as required, execute through an injected
deterministic tool callable, return reviewable artifact references, pass output
guardrails, and write audit events for every planning, policy, approval,
execution, validation, guardrail, recovery, and completion step.

## Guardrails

Codex runtime output is assistant output, not biomedical evidence. The runtime
blocks attempts to:

- invent biomedical evidence, assay results, citations, or molecules outside
  the generation pipeline
- change scores directly
- approve stage gates or campaign advancement
- bypass deterministic validators, RBAC, policy, or guardrails
- provide medical advice, lab protocols, synthesis instructions, dosing, or
  patient treatment guidance
- claim molecules are safe, active, effective, binding, or synthesizable

Tools may raise recoverable failures for common transient issues. The
`ActionExecutor` retries only within the registered tool's retry budget and
records both the failure and retry in the audit trail.

## Example

```python
from molecule_ranker.codex_runtime import CodexRuntimeAgent, RuntimeContext, ToolRegistry

registry = ToolRegistry()
# Register deterministic molecule-ranker ToolSpec instances at platform startup.

result = CodexRuntimeAgent(registry=registry).run(
    "Rank project candidates and export a reviewer report.",
    RuntimeContext(
        actor_id="user-1",
        org_id="org-1",
        project_id="project-1",
        permissions={"ranking:run", "reports:export"},
    ),
)
```

The result includes the plan, per-action steps, pending approvals when present,
guardrail warnings, review outputs, and complete structured audit events.
