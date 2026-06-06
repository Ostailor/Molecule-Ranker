# Troubleshooting

Use this guide to diagnose V3 workflow, validation, governance, and release-gate
issues.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Discover Fails

Check:

- `trace.json` for the failing step.
- CLI warnings and partial-success messages.
- `e2e_validation.json` for validation status.
- `v3_result_certification.json` for blocking findings.
- Whether the selected mode permits the requested action.

## Missing Artifacts

Expected artifacts depend on enabled features. For example,
`generated_candidates.json` appears only when generation is enabled, and
`generated_antibodies.json` appears only when antibody generation is enabled.

## Approval Required

If the workflow stops at an approval gate, review the approval summary and
human governance matrix. Codex cannot self-approve.

## Release Gate Fails

Common causes:

- Version is not `3.0.0`.
- Evidence marker is missing or failed.
- V3 validation failed.
- Mocked discover failed.
- Result certification failed.
- Boundary tests failed.
- Safety case or residual risk register missing.
- Required demos or docs missing.

## Live Mode Refuses Writes

`read_only_live` refuses external writes by design. Use `write_approved_live`
only when the approval policy, audit logging, and human approval are in place.

## Unsafe Output Appears

Treat medical advice, clinical validation claims, lab protocols, synthesis
instructions, dosing, or unsupported generated claims as release-blocking
failures.

