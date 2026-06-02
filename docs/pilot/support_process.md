# Support Process

## Intake

Collect only operational context:

- User ID or email.
- Project ID.
- Page or command.
- Job ID, artifact ID, or request ID.
- Expected behavior.
- Observed behavior.
- Screenshot or redacted log excerpt if needed.

Do not request credentials, tokens, cache files, or raw private payloads.

## Triage

1. Check `/dashboard/admin/support`.
2. Review failed jobs and dead-letter jobs.
3. Review recent alerts.
4. Confirm whether the issue is auth, project access, artifact availability, worker health, or UI clarity.
5. Generate a support bundle only when needed for diagnosis.

## Feedback Capture

```bash
uv run molecule-ranker feedback submit \
  "Short issue summary" \
  --root "$PILOT_ROOT" \
  --project-id "$PROJECT_ID" \
  --page-or-command "$PAGE_OR_COMMAND" \
  --severity medium
```

Feedback is operational input, not scientific evidence.

## Support Bundle

```bash
uv run molecule-ranker support bundle \
  --root "$PILOT_ROOT" \
  --output "$PILOT_ROOT/support_bundle.zip"
```

The bundle should include readiness, health, recent jobs, recent errors, artifact
hashes, validation summaries, performance summaries, and redacted logs. It must
exclude credentials, cache payloads, raw private payloads unless explicitly
approved, and unredacted Codex transcripts.

## Closure

Close a support item only after:

- User impact is understood.
- Root cause or workaround is recorded.
- Follow-up owner is assigned if needed.
- Feedback or bug report is filed.
- No secrets were retained in support notes.

