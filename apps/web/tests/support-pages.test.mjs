import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("support and admin pages", () => {
  it("usage page renders real organization usage and later-release placeholders", () => {
    const page = read("src/app/usage/page.tsx");

    for (const text of [
      "requireOrganizationMember",
      "getUsageSummaryForOrg",
      "getPlanUsageLimits",
      "productUsageActionLabels.create_project",
      "productUsageActionLabels.feedback_create",
      "Later release placeholders",
      "Release V0.5",
      "No payment provider connected",
      "does not enforce paid subscriptions",
    ]) {
      assert.match(page, new RegExp(text));
    }

    assert.doesNotMatch(page, /mock-data|resultBundles|usageSummary|PLACEHOLDER_V0_1_USAGE/);
  });

  it("usage page displays configured plan limits from product usage config", () => {
    const page = read("src/app/usage/page.tsx");
    const usage = read("src/lib/product/usage.ts");

    for (const text of [
      "free_internal",
      "pilot",
      "trial",
      "create_project",
      "feedback_create",
      "onboarding_complete",
      "run_discovery",
      "generate_hypotheses",
      "export_result",
      "codex_task",
      "Release V0.5",
    ]) {
      assert.match(`${page}\n${usage}`, new RegExp(text));
    }

    assert.match(page, /formatLimit\(planLimits\.create_project\)/);
    assert.match(page, /formatLimit\(planLimits\.feedback_create\)/);
  });

  it("account page renders profile, organization, acknowledgement, and legal links", () => {
    const page = read("src/app/account/page.tsx");

    for (const text of [
      "Profile placeholder",
      "Organization placeholder",
      "Research-use acknowledgement",
      "Terms placeholder",
      "Privacy placeholder",
      "/terms-placeholder",
      "/privacy-placeholder",
    ]) {
      assert.match(page, new RegExp(text));
    }
  });

  it("feedback mock success works locally without backend submit", () => {
    const page = read("src/app/feedback/page.tsx");
    const form = read("src/components/feedback/feedback-form-mock.tsx");

    assert.match(page, /FeedbackFormMock/);
    assert.match(form, /Feedback form mock/);
    assert.match(form, /Category/);
    assert.match(form, /Message/);
    assert.match(form, /Contact email placeholder/);
    assert.match(form, /setSubmitted\(true\)/);
    assert.match(form, /Feedback saved locally/);
    assert.match(form, /No backend submit yet/);
    assert.doesNotMatch(form, /\bfetch\s*\(/);
    assert.doesNotMatch(form, /type="submit"/);
    assert.doesNotMatch(form, /localStorage|sessionStorage|document\.cookie/);
  });

  it("admin page uses owner/admin auth and org-scoped data", () => {
    const page = read("src/app/admin/page.tsx");

    for (const text of [
      "requireAdminRole",
      "context.organization.id",
      "product_memberships",
      "product_profiles",
      "product_projects",
      "product_usage_events",
      "product_feedback",
      "Members list",
      "Workspace status",
      "Feature flags",
    ]) {
      assert.match(page, new RegExp(text));
    }

    assert.match(page, /\.eq\("organization_id", organizationId\)/);
    assert.doesNotMatch(page, /Pilot users summary mock|Projects\/runs mock|PLACEHOLDER_V0_1_ADMIN/);
    assert.doesNotMatch(page, /SUPABASE_SERVICE_ROLE_KEY|service_role|engine|raw internal/i);
  });

  it("admin page keeps later-release actions disabled", () => {
    const page = read("src/app/admin/page.tsx");

    for (const text of ["Invite user", "Manage roles", "Billing", "Disabled", "V0.5"]) {
      assert.match(page, new RegExp(text));
    }

    assert.match(page, /Role changes are intentionally disabled in V0\.2/);
  });
});
