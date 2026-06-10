# V0.3 Engine Boundary

Release V0.3 connects the product web app to the Dev V3.0 discovery engine only
through a product-safe boundary. The product app must treat the engine as an
internal execution dependency, not as a user-facing workflow surface.

## Boundary Principles

- Product APIs call a product-safe engine wrapper, not raw Dev V3.0 internals.
- Product APIs do not expose raw engine internals in responses, artifacts, UI, or
  normal-user diagnostics.
- Product users see result bundles and summary artifacts, not raw traces.
- Admins may see redacted diagnostics when needed for support or repair.
- Engine failures become safe product errors with no stack traces, raw logs,
  prompts, secrets, or transcript content.
- Engine artifacts are filtered before user exposure and stored only after
  product guardrails are applied.

## Allowed V0.3 Engine Command

The V0.3 wrapper may call `molecule-ranker discover` or an equivalent existing
end-to-end discovery command when that command can run within the same product
safety envelope.

Allowed execution modes:

- `mocked`
- `dry_run`
- `read_only_live`

Disabled execution capabilities:

- `write_approved_live`
- External writes.
- External integrations that write or mutate third-party systems.
- Antibody generation.
- Unbounded generated hypotheses.

Generated hypotheses must use a low product-safe limit. The output directory
must be isolated per organization, project, and run, using a path shape equivalent
to:

```text
<isolated-output-root>/<organization_id>/<project_id>/<run_id>/
```

The wrapper must not read from or write to shared cache locations as product run
artifacts. Any temporary engine files must be filtered before artifact storage.

## Product-Safe Outputs

The wrapper may publish these product-safe artifacts after filtering:

- Result bundle JSON.
- Result bundle Markdown.
- Candidate summary.
- Generated hypothesis summary.
- Evidence summary.
- Validation summary.
- Redacted trace for admin-only diagnostics.

Product-safe artifacts should be stored as `product_run_artifacts` rows with
tenant identifiers for organization, project, and run. Admin-only diagnostics
must set `admin_only = true`.

## Hidden From Normal Users

Normal product users must not receive:

- Raw AgentGraph state.
- Raw Codex transcripts.
- Raw tool logs.
- Raw repair logs.
- Raw governance internals.
- Cache files.
- Secrets.
- External credential details.

These materials must not appear in normal API responses, result pages, product
artifacts, support copy, browser logs, or user-visible error messages.

## Run Lifecycle

Discovery runs use the persisted `product_runs.status` lifecycle:

```text
queued -> running -> succeeded
                 -> failed
                 -> partially_succeeded
                 -> cancelled
```

`queued` means the product API accepted a guarded request and persisted a run
record. `running` means the wrapper is executing or the synchronous mocked
runner is preparing product-safe artifacts. Terminal states are `succeeded`,
`failed`, `partially_succeeded`, and `cancelled`.

## Failure Handling

Engine exceptions, validation failures, command failures, and artifact filtering
failures must be converted into safe product errors. The user-facing error should
explain that the bounded workflow could not prepare a result bundle and should
not expose engine command lines, stack traces, stdout, stderr, raw logs, prompts,
tool payloads, or credential details.

If redacted diagnostics are stored, they must be scoped to the same organization,
project, and run and marked admin-only.

## V0.3 Limitations

- V0.3 does not include production-grade background queue infrastructure.
- V0.3 is not billing-gated and does not include Stripe or paid subscriptions.
- The result viewer is summary-level until V0.4.
- Full candidate, evidence, generated hypothesis, and advanced artifact viewers
  remain out of scope.
- `read_only_live` remains optional and must be enabled only after the engine
  wrapper is reviewed for safe read-only behavior.
