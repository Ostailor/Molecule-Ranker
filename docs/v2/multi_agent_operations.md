# V2.4 Multi-Agent Scientific Operations

V2.4 adds specialized Codex subagents for operational delegation across the
existing molecule-ranker runtime-agent and governed tool ecosystem, plus
self-evaluation and repair-loop agents for recoverable workflow failures.

Subagents are operational specialists. They are not scientific truth sources.
Every delegated task remains inside the V2 runtime controls: RBAC, policy,
approved tools, sandbox profiles, approval gates, artifact validation,
guardrail checks, and audit logs.

## Specialist Roster

- Program management
- Evidence review
- Molecule design
- Developability and safety triage
- Experimental feedback
- Predictive modeling
- Structure workflow review
- Knowledge graph reasoning
- Hypothesis generation
- Portfolio and campaign planning
- Integration operations
- Evaluation and validation
- Guardrail and safety review
- Platform operations
- Self-evaluation
- Failure diagnosis
- Repair planning
- Repair execution
- Regression checking

## Delegation Contract

A specialist task records:

- delegated objective
- specialist identity and sandbox profile
- scoped artifact IDs
- approved runtime tools visible to the specialist
- runtime action plan
- tool results
- structured specialist output
- peer critiques and human-escalation records when present
- audit events

Specialists may inspect scoped artifacts, call approved tools, produce
structured outputs, critique each other, and escalate to human review.

## V2.4 Repair Loop

The repair loop evaluates plans before execution, evaluates outputs after
execution, diagnoses failed tools, failed jobs, failed validations, missing
artifacts, and guardrail failures, proposes repair plans, executes safe
deterministic repairs when policy allows, requests human approval for risky
repairs, retries recoverable workflows with bounded policies, records reusable
repair patterns, reruns regression checks, and writes repair audit reports.

Repair is operational. Agents may inspect logs and artifacts, rerun
deterministic tools, regenerate reports from existing artifacts, rerun
validation, request missing user input, and create engineering repair plans.
Agents may not invent evidence, assay results, citations, molecules, graph
facts, benchmark metrics, or scientific scores.

## Hard Boundaries

Specialists cannot:

- invent evidence, assay results, citations, molecules, graph facts, model
  metrics, docking scores, campaign outcomes, or benchmark results
- approve stage gates, campaign advancement, external writes,
  generated-molecule assay advancement, or destructive actions
- bypass deterministic validators, RBAC, policy, approvals, artifact
  validation, guardrails, approved tools, or sandbox boundaries
- approve their own repairs or hide guardrail failures
- provide medical advice, lab protocols, synthesis instructions, dosing, or
  patient treatment guidance

All specialist outputs must be artifact-grounded, schema-validated,
guardrail-checked, and auditable before they can become runtime artifacts.

## CLI

```bash
uv run molecule-ranker agent specialists

uv run molecule-ranker agent delegate \
  --specialist-id evidence-reviewer \
  --goal "Review ranking evidence and identify source-backed review questions" \
  --artifact-id ranking-run-001 \
  --autonomy dry_run
```
