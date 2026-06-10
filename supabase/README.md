# Supabase Schema

This directory contains the Release V0.3 Supabase schema for authentication,
users, organizations, memberships, roles, tenant-scoped projects, usage events,
feedback, bounded discovery runs, and product-safe run artifacts.

## Files

- `migrations/0001_product_auth_schema.sql` creates the V0.2 product auth schema.
- `migrations/0002_product_discovery_runs.sql` adds the V0.3 `product_runs`
  and `product_run_artifacts` tables with tenant-scoped RLS policies.
- `seed.sql` is intentionally not present yet. Do not add seed users,
  organizations, secrets, billing records, patient data, or PHI fixtures.

## Security Boundaries

The migration enables and forces Row Level Security on every product table.
Policies use Supabase `auth.uid()` plus active organization memberships to
isolate tenant data.

Run artifacts intentionally store only database-backed product-safe summaries
or explicitly declared storage pointers. Raw engine internals, AgentGraph state,
Codex transcripts, traces, logs, external write payloads, cache paths, and
integration secrets must not be stored in these tables.

The schema intentionally does not include:

- Billing or Stripe tables.
- Patient or PHI tables.
- Clinical workflow tables.
- Secrets, API keys, service role keys, or token storage.

Service role keys must stay outside this repository and must never be used in
browser-exposed environment variables, client code, logs, prompts, or support
bundles.

## Local Application

Use `apps/web/.env.example` as the web app environment template. Put local values
in `apps/web/.env.local`; local env files are gitignored.
