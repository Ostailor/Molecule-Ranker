# Run Discovery Workflow

The primary V3 command is `molecule-ranker discover`. It runs a governed
`full_discovery_loop` and emits a V3 result bundle.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Basic Command

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode read_only_live \
  --output-dir results/parkinson-v3-demo
```

## Safe Defaults

By default, V3 uses:

- `dry_run` mode unless another mode is provided.
- Generation disabled.
- Antibody generation disabled.
- External writes disabled.
- Codex autonomy set to `execute_with_approval`.
- No campaign activation.
- No stage-gate approval by Codex.
- No generated-molecule advancement without review.

## Modes

- `mocked`: deterministic synthetic fixtures for workflow validation.
- `dry_run`: real planning flow without external writes.
- `read_only_live`: read-only live retrieval where configured.
- `write_approved_live`: live writes only when approval policy permits them.

## Options

```text
--project-id
--mode mocked|dry_run|read_only_live|write_approved_live
--enable-generation
--enable-biologics
--enable-antibody-generation
--enable-structure
--enable-integrations
--enable-codex-summary
--autonomy observe_only|suggest_only|execute_safe_tools|execute_with_approval|supervised_auto
--require-approval
--output-dir
--json
```

## Expected Artifacts

Typical V3 output includes:

- `candidates.json`
- `developability.json`
- `literature_evidence.json`
- `graph.json`
- `hypotheses.json`
- `portfolio_optimization.json`
- `campaign_plan.json`
- `review_queue.json`
- `evaluation_report.json`
- `e2e_lineage.json`
- `e2e_validation.json`
- `v3_result_bundle.json`
- `v3_result_bundle.md`
- `v3_result_bundle.zip`
- `v3_result_certification.json`
- `trace.json`

Optional artifacts include:

- `generated_candidates.json` when generation is enabled.
- `biologic_candidates.json` when biologics is enabled.
- `generated_antibodies.json` when antibody generation is enabled.

## Reading The CLI Output

The CLI displays progress, current agent activity, artifacts produced so far,
warnings, approval needs, recovery suggestions, and final summaries. Treat
partial success as useful operational information, not as a passed result.

