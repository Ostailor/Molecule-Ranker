# V3 Documentation

molecule-ranker V3.0 is an autonomous discovery operating system for internal
research planning. It provides one-command governed workflows, result bundles,
human approval checkpoints, Codex runtime orchestration through approved tools,
multi-agent coordination, and release validation artifacts.

V3.0 does not add major new scientific capability. It stabilizes the V2.9
scientific workflow surface into a governed operating model for planning,
review, traceability, certification, and enterprise operations.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Start Here

- [Quickstart](quickstart.md)
- [Run Discovery Workflow](run_discovery_workflow.md)
- [Interpret Result Bundle](interpret_result_bundle.md)
- [Human Governance](human_governance.md)
- [Agent Runtime](agent_runtime.md)
- [Validation and Certification](validation_and_certification.md)
- [Safety Boundaries](safety_boundaries.md)

## Topic Guides

- [Generated Hypotheses](generated_hypotheses.md)
- [Biologics Track](biologics_track.md)
- [Integrations](integrations.md)
- [Campaign Co-Pilot](campaign_copilot.md)
- [Admin Operations](admin_operations.md)
- [Troubleshooting](troubleshooting.md)
- [FAQ](faq.md)

## Core Commands

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode read_only_live \
  --output-dir results/parkinson-v3-demo
```

```bash
molecule-ranker validate v3 --output-dir .molecule-ranker/validation/v3
molecule-ranker v3 release-gate --output-dir .molecule-ranker/v3_release_gate
```

## What V3 Produces

A V3 discovery run produces a research-planning bundle with ranked candidates,
evidence summaries, generated hypotheses when enabled, biologics outputs when
enabled, review queues, governance status, lineage, validation, certification,
and trace artifacts. These outputs help organize internal research decisions.
They do not prove activity, binding, safety, efficacy, manufacturability,
therapeutic value, or clinical readiness.

