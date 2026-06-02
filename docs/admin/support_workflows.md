# Admin Support Workflows

Support work should diagnose platform operation without exposing sensitive
material or changing scientific decisions.

Allowed support artifacts include version output, readiness status, metrics
names, redacted job IDs, redacted audit summaries, and support bundle manifests.
Do not collect environment variables, cache files, credentials, API keys,
service tokens, raw connector payloads, or plaintext secrets.

Support staff may help users retry, resume, or cancel jobs according to RBAC and
audit policy. They must not reinterpret Codex output as evidence, assay results,
molecules, scores, benchmark results, or decisions.
