# Monitoring And Alerting

Monitor V1.9 pilots with platform and workflow signals:

- API request latency and error rates.
- Job queued, running, failed, cancelled, and guardrail-failed counts.
- Worker duration and failure summaries.
- Codex task counts and guardrail failures.
- Artifact write counts and storage health.
- Auth failures and suspicious audit activity.

Alerts should include request IDs and redacted job or project identifiers only.
Do not put secrets, environment variables, cache contents, credentials, API keys,
service tokens, raw external payloads, assay values, or private data into alert
messages.
