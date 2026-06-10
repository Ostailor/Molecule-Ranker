import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("product feature flags", () => {
  it("defines release defaults and safe client flags", () => {
    const flags = read("src/lib/product/feature-flags.ts");

    assert.ok(existsSync(join(root, "src/lib/product/feature-flags.ts")));
    assert.match(flags, /releaseDefaultProductFeatureFlags/);
    assert.match(flags, /getClientSafeProductFeatureFlags/);
    assert.match(flags, /clientSafeFlagKeys/);
    assert.match(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_DISCOVERY_RUNS_PLACEHOLDER/);
    assert.match(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_GENERATED_HYPOTHESES_VIEWER/);
    assert.match(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_BIOLOGICS_VIEWER/);
    assert.match(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_EXPORTS_PLACEHOLDER/);
  });

  it("keeps risky features disabled by default and after overrides", () => {
    const flags = read("src/lib/product/feature-flags.ts");

    assert.match(flags, /antibodyGeneration: false/);
    assert.match(flags, /externalWrites: false/);
    assert.match(flags, /stripeBilling: false/);
    assert.match(flags, /externalIntegrations: false/);
    assert.match(flags, /forceRiskyFlagsDisabled/);
    assert.match(flags, /riskyAlwaysDisabledFlags/);
    assert.doesNotMatch(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_ANTIBODY_GENERATION/);
    assert.doesNotMatch(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_EXTERNAL_WRITES/);
    assert.doesNotMatch(flags, /NEXT_PUBLIC_PRODUCT_FEATURE_STRIPE_BILLING[^_]/);
  });

  it("wires placeholders and viewers through product flags", () => {
    const runForm = read("src/components/runs/start-discovery-run-form.tsx");
    const generatedPage = read("src/app/projects/[projectId]/runs/[runId]/generated/page.tsx");
    const resultOverview = read("src/components/runs/result-bundle-overview.tsx");
    const dashboard = read("src/components/dashboard/dashboard-overview.tsx");
    const usage = read("src/app/usage/page.tsx");

    assert.match(runForm, /productFeatureFlags\.discoveryRunsPlaceholder/);
    assert.match(runForm, /productFeatureFlags\.generatedHypothesesViewer/);
    assert.match(runForm, /productFeatureFlags\.exportsPlaceholder/);
    assert.match(generatedPage, /productFeatureFlags\.generatedHypothesesViewer/);
    assert.match(resultOverview, /productFeatureFlags\.generatedHypothesesViewer/);
    assert.match(resultOverview, /Artifact list/);
    assert.match(dashboard, /productFeatureFlags\.discoveryRunsPlaceholder/);
    assert.match(usage, /productFeatureFlags\.stripeBillingPlaceholder/);
  });

  it("keeps admin dashboard navigation role-gated", () => {
    const flags = read("src/lib/product/feature-flags.ts");
    const shell = read("src/components/layout/app-shell.tsx");
    const sideNav = read("src/components/layout/side-nav.tsx");
    const mobileNav = read("src/components/layout/mobile-nav.tsx");
    const adminPage = read("src/app/admin/page.tsx");

    assert.match(flags, /canShowAdminDashboard/);
    assert.match(flags, /canAccessAdmin\(role \?\? "viewer"\)/);
    assert.match(shell, /userRole\?: ProductRole \| null/);
    assert.match(shell, /<SideNav userRole=\{userRole\} \/>/);
    assert.match(sideNav, /const showAdminNav = canShowAdminDashboard\(userRole\)/);
    assert.match(mobileNav, /const showAdminNav = canShowAdminDashboard\(userRole\)/);
    assert.match(adminPage, /requireAdminRole\(supabase\)/);
    assert.match(adminPage, /<AppShell userRole=\{context\.role\}>/);
  });
});
