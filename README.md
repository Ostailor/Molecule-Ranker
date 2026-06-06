# molecule-ranker

`molecule-ranker` is a research-planning tool for transparent existing-molecule
and existing-antibody ranking, campaign workflow management, and governed Codex runtime-agent
operations. It resolves a disease through public biomedical data sources,
retrieves evidence-backed targets and existing small-molecule or biologics candidates, ranks candidates as
research hypotheses, and keeps autonomous agent actions inside approved policy,
tool, artifact, budget, certification, and safety boundaries.

The project is for research planning and operational oversight only. It does not provide medical advice.
It does not provide lab protocols, synthesis
instructions, dosing guidance, patient guidance, clinical claims, fabricated
evidence, fabricated assay results, fabricated citations, fabricated molecules,
fabricated graph facts, fabricated metrics, or fabricated approvals.

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

The current version is **2.8.0**.

V2.8.0 adds a governed biologics and antibody discovery track while preserving
the V2.7 small-molecule and E2E workflow capabilities. Existing antibodies and
biologics can be retrieved and ranked when records are source-backed. Antibody
sequences can be validated, numbered, annotated for CDRs, checked for novelty
against configured sources, and triaged with antibody-specific heuristic
developability signals.

Antibody generation is disabled by default. Generated antibody candidates are
computational hypotheses only. Generation requires approved generators/tools,
deterministic sequence validation, numbering/CDR annotation where possible,
novelty checks, developability triage, review gates, and lineage in result
bundles. No generated antibody is claimed to bind, neutralize, treat, cure, be
safe, be developable, or be manufacturable. The system does not provide
expression, purification, immunization, wet-lab, dosing, or clinical protocols.
Exact imported experimental evidence is required for direct support, and review
gates plus governance apply before any advancement decision.

The system can run governed workflows from a disease or project objective to
ranked small-molecule and antibody candidates, generated small-molecule
hypotheses, optional approved-plugin antibody generation plans, review
workspaces, portfolio and campaign plans, evaluation artifacts, lineage records,
and result bundles. These workflows are auditable, resumable, and constrained by
policy, approval, validation, and artifact-contract checks.

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
policy requires it.

## End-to-End Workflows

V2.8 workflows run in four modes:

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

Run the deterministic V2.8 E2E eval suite:

```bash
uv run molecule-ranker e2e eval --suite default
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

- V2.8: governed biologics and antibody discovery track.
- V2.9: V3 readiness and autonomy validation.
- V3.0: autonomous discovery operating system with validated human-governed agentic workflows.
