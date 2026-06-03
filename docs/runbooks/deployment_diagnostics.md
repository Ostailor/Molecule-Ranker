# Deployment Diagnostics

Run diagnostics before and during V1.9 pilots:

- Check `/version`, `/health`, `/ready`, and `/metrics`.
- Confirm version `2.0.0` and V1 contract identifiers.
- Confirm worker queues can enqueue, claim, fail, cancel, and record audit
  events in a non-live test project.
- Confirm release checks pass without requiring live external services.
- Confirm logs redact secrets and include request IDs.
- Confirm support bundle manifests do not include file contents, environment
  variables, cache files, API keys, service tokens, or credentials.

Diagnostics do not validate molecule safety, activity, binding, efficacy,
synthesizability, or clinical utility.
