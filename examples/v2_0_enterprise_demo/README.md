# V2.0 Enterprise Demo

This demo reuses the V1.9 synthetic hosted pilot demo assets as the V2.0
enterprise certification walkthrough. It is offline, synthetic, and
non-biomedical.

## Workflow

```bash
./scripts/bootstrap.sh
./scripts/run_demo_workflows.sh
./scripts/generate_support_bundle.sh
```

The scripts delegate to `examples/v1_9_pilot_demo/scripts/` so the enterprise
demo exercises the same hosted stack, project seed data, workflow run, feedback
capture, readiness checks, and redacted support bundle generation that V2.0
certifies.

## Boundaries

The demo does not contain real assay results, real citations, medical advice,
dosage guidance, patient treatment guidance, synthesis instructions, lab
protocols, or generated molecule activity/safety/efficacy claims.
