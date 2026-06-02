# Support Bundle Generation

V1.9 support bundles are manifests, not evidence packages. They list diagnostic
file paths, sizes, and readiness context so support can triage deployment and
workflow issues.

Support bundles must not include file contents by default. They must not include
environment variables, cache files, credentials, API keys, service tokens,
plaintext secrets, raw connector payloads, or private assay data.

Use the manifest to identify which safe diagnostics are available. If deeper
inspection is needed, request the smallest redacted artifact necessary and keep
scientific outputs separate from support notes.
