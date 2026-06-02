# V1.9 Enterprise Pilot Demo

This directory contains a local, synthetic-only V1.9 pilot demo environment.
It is intended for internal pilot onboarding, dashboard smoke checks, readiness
checks, and support-bundle workflow demonstrations.

The demo does not use live external APIs or real Codex execution. All data under
`synthetic_data/` is fake and uses synthetic source identifiers such as
`SYN-SRC-001`. It contains no real PMIDs, no real DOIs, no medical advice, no lab
protocols, no synthesis instructions, no dosing, no patient-treatment guidance,
and no committed secrets.

## What It Shows

- Hosted server and login-gated dashboard
- Runtime admin user creation
- Project creation
- Synthetic ranking artifacts
- Generated molecule hypothesis labels
- Review workflow records
- Synthetic experimental import artifact
- Graph, hypothesis, campaign, and evaluation summaries
- Pilot readiness report
- Redacted support bundle generation
- Failed-job/admin-support workflow visibility

## Local Quick Start

From the repository root:

```bash
examples/v1_9_pilot_demo/scripts/bootstrap.sh
examples/v1_9_pilot_demo/scripts/seed_demo_project.sh
examples/v1_9_pilot_demo/scripts/run_demo_workflows.sh
examples/v1_9_pilot_demo/scripts/generate_support_bundle.sh
```

Start the hosted server:

```bash
set -a
source examples/v1_9_pilot_demo/.demo_state/demo.env
set +a

uv run molecule-ranker serve \
  --root "$DEMO_ROOT" \
  --host "$DEMO_HOST" \
  --port "$DEMO_PORT" \
  --hosted \
  --auth-secret "$MOLECULE_RANKER_AUTH_SECRET" \
  --platform-db-path "$DEMO_DB_PATH"
```

Open the dashboard at `http://127.0.0.1:8765/dashboard`.

The bootstrap script writes generated local demo credentials under
`.demo_state/`. That directory is ignored by git. To view the generated admin
password:

```bash
cat examples/v1_9_pilot_demo/.demo_state/admin_password.txt
```

## Docker Compose

The compose file uses the local checkout mounted into a Python/uv container.
It runs bootstrap and seeding before starting the hosted server.

```bash
docker compose -f examples/v1_9_pilot_demo/docker-compose.yml up
```

Then open `http://127.0.0.1:8765/dashboard`.

## Generated Files

Runtime state is written to:

```text
examples/v1_9_pilot_demo/.demo_state/
```

Useful outputs:

- `.demo_state/results/synthetic-run-001/`
- `.demo_state/reports/pilot_readiness_report.md`
- `.demo_state/support/v1_9_pilot_demo_support_bundle.zip`
- `.demo_state/.molecule-ranker/validation/`

## Safety Boundaries

This demo is for platform readiness and workflow onboarding only.

- Synthetic candidates are not active, safe, effective, binding, synthesizable, or clinically useful.
- Generated molecules are computational hypotheses.
- Model and evaluation outputs are artifacts, not assay results or biomedical evidence.
- Codex output is assistant output and is separate from evidence, molecules, scores, benchmark results, and decisions.
- Support bundles redact sensitive-looking values and exclude cache payloads and raw assay payloads by default.

