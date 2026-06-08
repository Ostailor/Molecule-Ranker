# Supabase Schema

This directory contains the Release V0.2 Supabase schema for authentication,
users, organizations, memberships, roles, tenant-scoped projects, usage events,
and feedback.

## Files

- `migrations/0001_product_auth_schema.sql` creates the V0.2 product auth schema.
- `seed.sql` is intentionally not present yet. Do not add seed users,
  organizations, secrets, billing records, patient data, or PHI fixtures.

## Security Boundaries

The migration enables and forces Row Level Security on every product table.
Policies use Supabase `auth.uid()` plus active organization memberships to
isolate tenant data.

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
