import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("authenticated onboarding", () => {
  it("requires auth before rendering onboarding", () => {
    const page = read("src/app/onboarding/page.tsx");
    const middleware = read("src/lib/supabase/middleware.ts");

    assert.match(page, /requireUser\("\/login\?next=\/onboarding"\)/);
    assert.match(middleware, /"\/onboarding"/);
    assert.match(middleware, /protectedRoutePrefixes/);
  });

  it("requires research-use acknowledgement", () => {
    const form = read("src/components/onboarding/onboarding-form.tsx");
    const actions = read("src/lib/supabase/auth-actions.ts");

    assert.match(form, /name="researchUseAcknowledged"/);
    assert.match(form, /required/);
    assert.match(actions, /formData\.get\("researchUseAcknowledged"\) === "on"/);
    assert.match(actions, /Acknowledge the research-use boundary/);
  });

  it("updates profile and records acknowledgement timestamp", () => {
    const actions = read("src/lib/supabase/auth-actions.ts");

    assert.match(actions, /export async function finishOnboardingAction/);
    assert.match(actions, /\.from\("product_profiles"\)/);
    assert.match(actions, /display_name: displayName/);
    assert.match(actions, /onboarding_completed: true/);
    assert.match(actions, /research_use_acknowledged_at: now/);
    assert.match(actions, /const profileUpdatePayload = \{/);
    assert.match(actions, /\.update\(profileUpdatePayload\)\.eq\("id", user\.id\)/);
    assert.match(actions, /email,\n\s+\.\.\.profileUpdatePayload/);
    assert.match(actions, /recordUsageEvent\("onboarding_complete", 1, \{ use_case: useCase \}/);
  });

  it("creates missing organization and owner membership before redirecting to dashboard", () => {
    const actions = read("src/lib/supabase/auth-actions.ts");

    assert.match(actions, /\.from\("product_organizations"\)/);
    assert.match(actions, /owner_user_id: user\.id/);
    assert.match(actions, /\.from\("product_memberships"\)/);
    assert.match(actions, /role: "owner"/);
    assert.match(actions, /redirect\("\/dashboard"\)/);
  });
});
