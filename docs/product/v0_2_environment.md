# V0.2 Environment Variables

Release V0.2 uses Supabase Auth, Supabase Postgres, and Supabase Row Level
Security for the pilot auth path. Local web app configuration lives in
`apps/web/.env.local`, using `apps/web/.env.example` as the template.

Do not commit real secret values.

| Variable | Required | Scope | Notes |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Yes | Browser and server | Supabase project URL used by browser and SSR clients. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Yes | Browser and server | Browser-safe publishable key. The web app fails clearly when this is missing. |
| `PRODUCT_APP_URL` | Yes | Server and deployment config | Canonical app URL for auth redirects and callbacks. |
| `SUPABASE_SERVICE_ROLE_KEY` | Later server/admin only | Server only | Privileged key for controlled admin operations. Never import into `apps/web/src` browser or shared client code. |
| `SUPABASE_JWT_SECRET` | Optional | Server only | Use only if backend JWT verification is implemented outside Supabase helpers. |

`SUPABASE_SERVICE_ROLE_KEY` must never be used in client-side code, logs, Codex
prompts, support bundles, or browser-exposed env vars. It must never be prefixed
with `NEXT_PUBLIC_`.

V0.2 client creation uses only `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. Server-side user and claims helpers use
cookie-based Supabase SSR clients, not the service role key.
