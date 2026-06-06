# Admin Operations

V3 admin operations focus on safe defaults, evidence markers, validation,
release gates, support workflows, and governed runtime configuration.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Operational Checklist

1. Confirm package version is `3.0.0`.
2. Confirm package lock is present.
3. Run tests, ruff, pyright, and Docker build in CI.
4. Write evidence markers only after jobs pass.
5. Run mocked discovery.
6. Run `molecule-ranker validate v3`.
7. Run `molecule-ranker v3 release-gate`.
8. Review safety case and residual risk register.
9. Confirm deployment and training docs are published.
10. Confirm required demos are present.

## Evidence Markers

The release gate can consume pass markers for expensive checks:

- `tests_pass_marker.json`
- `ruff_pass_marker.json`
- `pyright_pass_marker.json`
- `docker_build_pass_marker.json`
- `v3_validation_passes.json`

Markers should be created by CI after the corresponding job succeeds.

## Support Redaction

Support bundles with logs or transcripts require approval and redaction. Do not
include secrets, patient data, uncontrolled generated claims, lab instructions,
or dosing guidance.

## Deployment Posture

Use dry-run and read-only modes until access control, audit logging, approval
flows, integration credentials, support redaction, and rollback processes are
validated.

