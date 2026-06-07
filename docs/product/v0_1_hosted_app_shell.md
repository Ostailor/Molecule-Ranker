# V0.1 Hosted App Shell

V0.1 is the local and preview-ready frontend shell for the MolCreate pilot app.
It is a researcher-facing Next.js app that demonstrates the Release V1.0
information architecture, product language, mock workflows, and research-use
guardrails without connecting to real authentication, user storage, billing, or
backend execution.

## V0.1 Scope

V0.1 covers the hosted app shell and dashboard experience only:

- Establish the frontend app under `apps/web`.
- Show the product-safe workflow from project creation to discovery run review.
- Render mock result bundles, candidate ranking, evidence, generated hypotheses,
  usage, feedback, account, and admin placeholder surfaces.
- Keep all data synthetic and clearly marked for UI demonstration only.
- Keep research-use disclaimers visible across the shell.
- Keep the Python package isolated and runnable from the repository root.

## What V0.1 Includes

- Next.js App Router, TypeScript, Tailwind CSS, ESLint, and basic component
  structure.
- Product shell layout with top navigation, side navigation, mobile navigation,
  breadcrumbs, page headers, footer, and account placeholder.
- Landing page, login placeholder, onboarding placeholder, dashboard, projects,
  discovery run pages, result bundle overview, candidate pages, evidence page,
  generated hypotheses page, usage, account, feedback, and admin placeholder.
- Reusable UI components for buttons, cards, badges, alerts, tables, tabs,
  empty/loading/error states, stat cards, timelines, usage meters, score and
  confidence badges, warning lists, feature gates, and disclaimer banners.
- Synthetic mock data for pilot user, organization, projects, runs, result
  bundles, candidates, evidence, generated hypotheses, usage, feedback, and
  admin summary.
- Product copy guardrail tests that scan frontend source for unsafe marketing or
  product claims.

## What V0.1 Does Not Include

- Real authentication, sessions, user accounts, organization persistence, or
  role enforcement.
- Backend calls, run execution, live source retrieval, persisted projects,
  persisted feedback, or result bundle storage.
- Real biomedical identifiers, real citations, real source-backed evidence, or
  real generated structures.
- Billing, Stripe, payment plans, or production entitlements.
- Production deployment instructions.
- Medical advice, clinical decision support, regulated medical-product behavior,
  treatment guidance, synthesis planning, lab-protocol generation, or dosing
  guidance.

## Run The Web App Locally

Use npm inside the frontend package:

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:3000` for the landing page or
`http://localhost:3000/dashboard` for the mock dashboard.

For a local production-style preview:

```bash
cd apps/web
npm run build
npm run start -- -p 3000
```

If port `3000` is already in use, choose another local port, for example:

```bash
npm run dev -- -p 3001
```

## Run Tests

Run all web checks from the frontend package:

```bash
cd apps/web
npm test
npm run lint
npm run typecheck
npm run build
```

Run the product shell test directly:

```bash
cd apps/web
node --test tests/shell.test.mjs
```

Run the product copy guardrail test directly:

```bash
cd apps/web
node --test tests/product-copy-guardrails.test.mjs
```

Python package checks remain at the repository root:

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

## Inspect Mock Data

Mock UI data lives in `apps/web/src/lib/mock-data.ts`.

All mock records are synthetic and include:

```ts
metadata: { synthetic: true, for_ui_demo_only: true }
```

The mock data intentionally uses names such as `ExampleDiseaseA`,
`ExampleTargetA`, `ExampleCandidateA`, `Example Evidence Source`, and
`Synthetic generated hypothesis`. It must not include real-looking PMIDs, DOIs,
ChEMBL IDs, PubChem CIDs, or disease-specific claims that look source-backed.

## Feature Flags

Feature flags live in `apps/web/src/lib/feature-flags.ts`.

V0.1 uses feature flags to show or hide placeholder capabilities such as
generated hypotheses. Disabled features should render clear placeholder states
instead of disappearing silently.

## Disclaimers

Research-use disclaimer copy lives in `apps/web/src/lib/disclaimers.ts`.

The persistent shell banner is rendered through the layout components, and page
or section-level reminders use the reusable disclaimer components. Keep
disclaimer text centralized so later releases can update legal and product
language without hunting through page components.

## Mapping To Release V1.0 Pilot App

V0.1 maps the expected Release V1.0 pilot app shape without production data or
backend behavior:

- Dashboard becomes the pilot user's workspace home.
- Projects become organization-scoped research planning containers.
- Discovery run pages become the bounded workflow start and status experience.
- Result bundle pages become the auditable review surface.
- Candidate, evidence, and generated hypothesis pages become the human review
  workspace.
- Usage, account, feedback, and admin surfaces become the pilot operations and
  support layer.

Release V1.0 should replace synthetic data with product APIs, persisted records,
real source provenance, export artifacts, role-aware admin access, and audited
human review flows while preserving the same research-use boundary.

## Next Step: V0.2 Auth, Users, And Organizations

V0.2 should add the identity and tenancy layer:

- Real sign-in and sign-out flow.
- User profile model.
- Organization model.
- User-to-organization membership.
- Role-aware navigation and admin gating.
- Persisted research-use acknowledgement.
- Server-side tenant isolation through the product API boundary.

V0.2 should still avoid live discovery execution, billing, production
deployment, and regulated medical-product behavior.
