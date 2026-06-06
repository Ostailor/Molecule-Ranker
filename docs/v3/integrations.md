# Integrations

V3 integrations are governed by mode, approval policy, and external-write
controls. External writes are disabled by default.

## Common V3 Boundaries

- No medical advice.
- No clinical validation.
- No lab protocols.
- No synthesis instructions.
- No dosing.
- Generated hypotheses require independent validation and human review.
- Codex output is not scientific truth.

## Enable Integration Planning

```bash
molecule-ranker discover \
  --disease "Parkinson disease" \
  --mode dry_run \
  --enable-integrations \
  --output-dir results/parkinson-integrations
```

## Mode Behavior

- `mocked`: uses synthetic integration behavior.
- `dry_run`: plans integration actions without writing externally.
- `read_only_live`: permits configured read-only retrieval, not writes.
- `write_approved_live`: permits writes only when human approval is present.

## External Writes

External write approval is always required. Codex cannot approve writes. A
write without approval fails certification and blocks V3 success.

## Integration Outputs

Integration summaries appear in the result bundle and trace. They document sync
intent, read status, skipped writes, approval requirements, and warnings.

## Support Bundles

Support bundles containing logs or transcripts require approval and redaction.
Do not include secrets, patient data, or uncontrolled scientific claims.

