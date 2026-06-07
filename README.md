# molecule-ranker

`molecule-ranker` is an autonomous discovery operating system for internal
research planning with validated human-governed agentic workflows. V3.0 can run
governed end-to-end workflows from disease or project objective to auditable
result bundle. Codex agents operate approved tools through policy, approval,
validation, and artifact contracts.

The platform supports small molecules, generated small-molecule hypotheses,
existing biologics/antibodies, and governed antibody-generation planning when
explicitly enabled. Result bundles include candidate rankings, generated
hypotheses, evidence, review workspace, portfolio/campaign plan, evaluation,
lineage, guardrails, and certification.

V3.0 is not a clinical product. It does not provide medical advice. It does not
provide lab protocols. It does not provide synthesis instructions. It does not
provide dosing. It does not claim generated molecules or antibodies are active,
safe, effective, binding, developable, or manufacturable.

Exact imported experimental evidence and human review are required for direct
support. V3 validation artifacts are software/autonomy validation artifacts, not
clinical validation. The project also does not provide patient guidance,
clinical claims, fabricated evidence, fabricated assay results, fabricated
citations, fabricated molecules, fabricated graph facts, fabricated metrics, or
fabricated approvals.

## Quickstart

Run one-command V3 discovery with safe dry-run defaults:

```bash
molecule-ranker discover --disease "Parkinson disease" --mode dry_run
```

Run live read-only discovery and write a demo result bundle:

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode read_only_live \
  --output-dir results/parkinson-v3-demo
```

Run V3 validation and the final release gate:

```bash
molecule-ranker validate v3
molecule-ranker v3 release-gate
```

When running from a local checkout, prefix commands with `uv run` if the console
script is not installed in the active environment.

## Frontend Web App

The mock MolCreate frontend lives in `apps/web` and is intentionally isolated
from the Python package. It uses npm, Next.js App Router, TypeScript, Tailwind
CSS, and ESLint. See `docs/product/v0_1_hosted_app_shell.md` for the full V0.1
scope, mock-data locations, feature flags, disclaimers, and Release V1.0 pilot
mapping.

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:3000` for the landing page or
`http://localhost:3000/dashboard` for the mock dashboard.

Frontend checks:

```bash
cd apps/web
npm test
npm run lint
npm run typecheck
npm run build
```

Run the product shell test directly:

```bash
cd apps/web
node --test tests/shell.test.mjs
```

Python package checks remain rooted at the repository top level:

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

## What It Can Do Today

`molecule-ranker` currently supports governed computational discovery
operations across public/read-only biomedical sources, deterministic local
artifacts, and approved integration tools. Today it can:

- Resolve a disease name into a ranked research-planning workflow.
- Retrieve source-backed targets and existing molecules from public biomedical
  sources.
- Retrieve and rank existing antibody/biologics candidates when source data
  supports the records.
- Capture antibody target and antigen context, antibody sequence schemas,
  deterministic sequence validation, numbering/CDR annotations, exact-sequence
  novelty checks, antibody-specific developability heuristics, report cards,
  review gates, lineage, and biologics result-bundle summaries.
- Rank existing molecules as auditable research hypotheses, with provenance,
  warnings, and trace artifacts.
- Retrieve literature metadata and extracted claim records for review-oriented
  evidence summaries.
- Generate small-molecule analog hypotheses from source-backed seed molecules
  when generation is explicitly enabled.
- Plan antibody sequence generation only through approved model/tool plugins.
  Antibody generation is disabled by default; approved plugin outputs must be
  imported as computational hypotheses and pass deterministic validation,
  exact-sequence novelty checks, developability triage, review gates, and
  result-bundle lineage before review.
- Run deterministic chemical sanity checks, novelty checks, diversity filtering,
  and heuristic developability triage on generated small-molecule hypotheses.
- Create review queues, review workspaces, campaign/portfolio planning
  artifacts, evaluation artifacts, lineage records, and end-to-end result
  bundles.
- Operate external integrations in mocked, dry-run, read-only live, and
  write-approved live modes, with dry-run as the safe default.
- Enforce governance controls for external writes, generated-molecule
  advancement, capabilities, autonomy budgets, approval gates, run controls,
  kill switches, incident handling, and repair/resume workflows.
- Expose E2E workflow operations through the CLI, hosted API routes, and hosted
  dashboard views.

Generated small molecules are computational hypotheses only. They are not
evidence of binding, activity, safety, efficacy, novelty, patentability, or
clinical utility. Exact public-database novelty checks can rule out exact
matches in checked sources, but they do not prove global novelty.

Generated antibodies are computational hypotheses only. The system does not
claim generated antibodies bind, neutralize, treat, cure, are safe, are
developable, or are manufacturable. Exact imported experimental results are
required before any generated antibody has direct evidence.

## What It Cannot Do

The project also does not:

- Invent missing targets, molecules, external records, citations, assay results,
  graph facts, or scientific conclusions.
- Treat generated molecules as validated leads or advance them without required
  human review.
- Treat failed QC, imported unvalidated data, or Codex-authored summaries as
  scientific support.
- Perform lab execution, campaign activation, clinical interpretation, patient
  treatment selection, dosing, synthesis planning, or experimental protocol
  design.
- Provide wet-lab protocols, immunization protocols, expression or purification
  protocols, animal or human dosing, synthesis instructions, fabricated antibody
  sequences, fabricated assay results, fabricated citations, fabricated external
  records, fabricated epitopes, fabricated structures, or binding claims.

## Current Version

The current version is **3.0.0**.

V3.0.0 ships molecule-ranker as an autonomous discovery operating system with
validated human-governed agentic workflows. It focuses on one-command
end-to-end workflows, a stable autonomous runtime experience, validated result
bundles, human governance checkpoints, Codex operation through approved tools
only, useful multi-agent coordination by default, strong safety/governance/
reproducibility defaults, a production-ready V3 dashboard experience,
enterprise-ready documentation and training, and a release certification plus
V3 validation package.

V3.0 can run governed end-to-end workflows from disease/project objective to an
auditable result bundle. Codex agents operate approved tools through policy,
approval, validation, and artifact contracts. Result bundles include candidate
rankings, generated hypotheses, evidence, review workspace, portfolio/campaign
plan, evaluation, lineage, guardrails, and certification.

V3.0 does not add a new science modality or major new scientific capabilities.
It does not expand docking, generation, biologics, model training,
integrations, or graph reasoning except for stability, validation, usability,
and end-to-end coherence.

Antibody generation is disabled by default. Generated antibody candidates are
computational hypotheses only. Generation requires approved generators/tools,
deterministic sequence validation, numbering/CDR annotation where possible,
novelty checks, developability triage, review gates, and lineage in result
bundles. No generated antibody is claimed to bind, neutralize, treat, cure, be
safe, be developable, or be manufacturable. The system does not provide
expression, purification, immunization, wet-lab, dosing, or clinical protocols.
Exact imported experimental evidence is required for direct support, and review
gates plus governance apply before any advancement decision.

V3.0 includes the `AutonomyValidationSuite`, `V3ReadinessReport`,
`EndToEndResultCertification`, `HumanGovernanceMatrix`,
`AgentReliabilityScorecard`, `SafetyCaseReport`, `ResidualRiskRegister`,
`V3DemoProject`, `V3ReleaseCandidate` workflow, end-to-end autonomy red-team
suite, V3 performance/reliability gate, release certification package, and V3
discovery operating-system dashboard. These reports are software/autonomy
validation artifacts, not clinical validation or biomedical evidence.

The system can run governed workflows from a disease or project objective to
ranked small-molecule and antibody candidates, generated small-molecule
hypotheses, optional approved-plugin antibody generation plans, review
workspaces, portfolio and campaign plans, evaluation artifacts, lineage records,
and result bundles. These workflows are auditable, resumable, and constrained by
policy, approval, validation, and artifact-contract checks.

Codex remains a governed runtime agent, not a scientific truth source. Codex can
plan, execute approved tools, summarize validated artifacts, and help repair
workflow failures, but it cannot create biomedical evidence, assay results,
citations, graph facts, approvals, or scientific truth.

The autonomous campaign co-pilot is a campaign-management assistant, not a lab
executor or source of scientific truth. Failed QC is never treated as positive or negative evidence.
Generated molecules and generated antibodies remain computational hypotheses
until supported by exact imported evidence and human review.
Generated molecules remain computational hypotheses throughout deterministic
triage and review until exact imported evidence is linked.

Agents cannot self-certify, self-approve, self-grant capabilities, or approve
policy overrides. Higher autonomy requires active certification. External
writes, generated-molecule advancement, destructive actions, high-cost jobs,
and policy overrides require governance checks and human/admin approval where
policy requires it. External writes require approval, and human governance
remains required for high-risk actions.

## End-to-End Workflows

V3.0 workflows run in four modes:

- `mocked`: deterministic synthetic sources for local testing.
- `dry_run`: planned actions and simulated integration changes with no external writes.
- `read_only_live`: public or configured read-only sources only.
- `write_approved_live`: live writes only when explicit approval and governance permission exist.

Codex can operate the workflow through approved tools, but it cannot fabricate
missing data, assay results, citations, molecules, graph facts, external IDs,
or scientific conclusions. Imported integration data must pass deterministic
validation before it can affect evidence or scoring. Result bundles are
research and operations summaries; they are not evidence, clinical validation,
medical advice, or proof of activity, safety, or efficacy.

Run a mocked full workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow full_discovery_loop \
  --disease "Parkinson disease" \
  --mode mocked \
  --enable-generation \
  --enable-codex-summary
```

Run a mocked mixed small-molecule and antibody workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow full_discovery_loop_with_biologics \
  --disease "Parkinson disease" \
  --mode mocked
```

Antibody generation remains off unless an approved biologics plugin is explicitly
configured. Any plugin output must enter the system as a computational hypothesis
with deterministic validation, novelty checks, developability triage, expert
review gates, and lineage.

Run a dry-run integration workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow integration_sync_loop \
  --project-id project-1 \
  --mode dry_run \
  --enable-integrations \
  --dry-run
```

Run a read-only live ranking workflow:

```bash
uv run molecule-ranker e2e run \
  --workflow disease_to_ranked_candidates \
  --disease "Parkinson disease" \
  --project-id project-1 \
  --mode read_only_live
```

Resume a workflow:

```bash
uv run molecule-ranker e2e resume \
  --workflow-id e2e-workflow-id
```

Validate a workflow:

```bash
uv run molecule-ranker e2e validate \
  --workflow-id e2e-workflow-id
```

Generate or inspect the result bundle:

```bash
uv run molecule-ranker e2e bundle \
  --workflow-id e2e-workflow-id
```

View lineage:

```bash
uv run molecule-ranker e2e lineage \
  --workflow-id e2e-workflow-id
```

Run the deterministic V3.0 E2E eval suite:

```bash
uv run molecule-ranker e2e eval --suite default
```

For the primary V3 one-command workflow, prefer:

```bash
uv run molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode dry_run
```

The `discover` command produces a V3 result bundle with candidates,
generated-hypothesis artifacts when enabled, evidence summaries, review queues,
portfolio and campaign drafts, evaluation reports, lineage, guardrail
validation, trace data, and result certification.

## V3 Readiness And Autonomy Validation

V3.0 validates end-to-end workflows, autonomy boundaries, agent reliability,
result certification, safety cases, residual risk, V3 demo workflows, and V3
release-candidate readiness. The validation artifacts are platform and autonomy
evidence only. They are not clinical validation, biomedical evidence, medical
advice, or proof of binding, activity, safety, efficacy, manufacturability, or
therapeutic value.

Run the V3 readiness report:

```bash
uv run molecule-ranker validate v3-readiness \
  --output-dir .molecule-ranker/validation/v3-readiness
```

Run autonomy boundary red-team tests:

```bash
uv run molecule-ranker validate autonomy-boundaries
```

Run all built-in autonomy scenarios:

```bash
uv run molecule-ranker validate autonomy --all
```

Certify an E2E result through the autonomy validator. The JSON output includes
`result_certification`, which is platform/workflow certification, not scientific
validation:

```bash
uv run molecule-ranker validate autonomy \
  --scenario v3_full_demo_mocked \
  --json
```

Direct certification is also available from Python for a known workflow ID and
scenario:

```bash
uv run python - <<'PY'
from molecule_ranker.autonomy_validation import certify_e2e_result
from molecule_ranker.autonomy_validation.scenario_builder import get_builtin_autonomy_scenario

scenario = get_builtin_autonomy_scenario("v3_full_demo_mocked")
certification = certify_e2e_result("workflow-id", scenario)
print(certification.model_dump(mode="json"))
PY
```

Run the V3 release-candidate workflow:

```bash
uv run molecule-ranker v3 rc \
  --output-dir .molecule-ranker/v3_rc
```

Run the V3 demo project in mocked mode:

```bash
bash examples/v3_demo/run_mocked_demo.sh
```

Run the V3 demo validation:

```bash
bash examples/v3_demo/run_validation.sh
```

View the readiness dashboard payload:

```bash
uv run molecule-ranker v3 dashboard \
  --output-dir .molecule-ranker/validation/v3-dashboard \
  --json
```

Run the V3 performance/reliability gate:

```bash
uv run molecule-ranker validate v3-performance \
  --output-dir .molecule-ranker/validation/v3-performance
```

## Biologics And Antibody Track

The biologics track is governed by source provenance, deterministic validation,
review gates, and biologics-specific guardrails. It supports existing
antibody/biologic retrieval and ranking when evidence or registry records are
available. Sequence-specific analysis only runs when an actual amino-acid
sequence is retrieved, imported, or user supplied; missing antibody sequences are
not fabricated.

Use source-backed biologic records for local examples. Records may omit
sequences; sequence-specific validation, numbering, CDR annotation, novelty, and
developability triage are only available when an actual source/imported sequence
is present.

```json
{
  "biologic_candidates": [
    {
      "biologic_id": "<SOURCE_BACKED_BIOLOGIC_ID>",
      "name": "<SOURCE_BACKED_BIOLOGIC_NAME>",
      "biologic_type": "monoclonal_antibody",
      "origin": "existing",
      "target_symbols": ["<TARGET_SYMBOL>"],
      "antigen_names": ["<SOURCE_BACKED_ANTIGEN_NAME>"],
      "disease_name": "<DISEASE_NAME>",
      "identifiers": {"registry": "<SOURCE_RECORD_ID>"},
      "sequence": "<SOURCE_BACKED_AMINO_ACID_SEQUENCE_IF_AVAILABLE>",
      "chain_type": "heavy",
      "evidence_item_ids": ["<SOURCE_BACKED_EVIDENCE_ITEM_ID>"],
      "direct_experimental_evidence": false
    }
  ]
}
```

Retrieve existing biologics:

```bash
uv run molecule-ranker biologics retrieve \
  --target-symbol <TARGET_SYMBOL> \
  --disease "<DISEASE_NAME>" \
  --records /path/to/source_backed_biologics.json \
  --output-dir results/biologics-demo
```

Validate an antibody sequence:

```bash
uv run molecule-ranker biologics validate-sequence \
  --sequence "$SOURCE_BACKED_AMINO_ACID_SEQUENCE" \
  --sequence-id <SOURCE_BACKED_SEQUENCE_ID> \
  --chain-type heavy
```

Assess antibody developability heuristics:

```bash
uv run molecule-ranker biologics assess-developability \
  --sequence "$SOURCE_BACKED_AMINO_ACID_SEQUENCE" \
  --sequence-id <SOURCE_BACKED_SEQUENCE_ID> \
  --biologic-id <SOURCE_BACKED_BIOLOGIC_ID> \
  --chain-type heavy
```

Generate antibody hypotheses in mocked mode through the governed E2E workflow.
This is still disabled unless generation is explicitly requested, and any
generated output remains a computational hypothesis:

```bash
uv run molecule-ranker e2e run \
  --workflow biologics_discovery_loop \
  --disease "<DISEASE_NAME>" \
  --mode mocked \
  --enable-generation \
  --output-dir .molecule-ranker/e2e-biologics-demo
```

Run the biologics E2E workflow with generation disabled, which is the default:

```bash
uv run molecule-ranker e2e run \
  --workflow biologics_discovery_loop \
  --disease "<DISEASE_NAME>" \
  --mode mocked \
  --output-dir .molecule-ranker/e2e-biologics-demo
```

Create a biologics review packet from a review workspace:

```bash
uv run molecule-ranker review handoff \
  --workspace results/biologics-demo/review_workspace.json \
  --item-id <BIOLOGICS_REVIEW_ITEM_ID> \
  --reviewer-id biologics-reviewer-1 \
  --output results/biologics-demo/biologics_review_packet.json
```

Run biologics guardrails:

```bash
uv run molecule-ranker biologics validate-guardrails \
  --root .molecule-ranker/validation-demo
```

Equivalent validation is also available through:

```bash
uv run molecule-ranker validate biologics-guardrails \
  --root .molecule-ranker/validation-demo
```

## Install

Python 3.11+ is required.

```bash
uv sync
```

Check the CLI:

```bash
uv run molecule-ranker --help
uv run molecule-ranker version
```

## Run Ranking

Run an existing-molecule ranking job:

```bash
uv run molecule-ranker rank "Alzheimer disease" --top 10
```

Write outputs under `results/`:

```bash
uv run molecule-ranker rank "Alzheimer disease" \
  --top 10 \
  --output-dir results \
  --timeout 20 \
  --max-targets 25 \
  --max-molecules-per-target 10
```

Typical outputs:

```text
results/<disease_slug>/report.md
results/<disease_slug>/candidates.json
results/<disease_slug>/trace.json
```

The ranking workflow uses live public biomedical APIs. If required real data
cannot be retrieved, the workflow fails instead of inventing targets,
molecules, evidence, citations, or scores.

## Run Governance

Validate current governance behavior:

```bash
uv run molecule-ranker validate agent-governance
```

Run the default governance red-team eval suite:

```bash
uv run molecule-ranker governance eval --suite default
```

Simulate a governed action before enabling policy:

```bash
uv run molecule-ranker governance simulate \
  --agent-id runtime-agent-1 \
  --agent-type runtime_agent \
  --tool run_external_sync_write \
  --action run_external_sync_write \
  --tool-category integration \
  --side-effect-level external_write \
  --project-id project-1
```

Generate governance oversight artifacts:

```bash
uv run molecule-ranker governance report \
  --project-id project-1 \
  --output-dir .molecule-ranker/agent-governance/reports
```

Governance reports are operational oversight artifacts, not scientific
evidence.

## Run Tests

Run linting, type checking, and the full test suite:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

Run the deterministic release validation workflows:

```bash
uv run molecule-ranker validate release
```

## Roadmap

- V3.1: pilot feedback and usability refinement.
- V3.2: deeper enterprise integrations and deployment feedback.
- V3.3: scaled agent operations and cross-org governance.
- V4.0: production-grade autonomous discovery operations at scale.
