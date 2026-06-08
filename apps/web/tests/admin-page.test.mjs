import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("admin page authorization and tenant scope", () => {
  it("requires owner or admin access in the page and middleware", () => {
    const page = read("src/app/admin/page.tsx");
    const middleware = read("src/lib/supabase/middleware.ts");
    const authContext = read("src/lib/product/auth-context.ts");

    assert.match(page, /requireAdminRole\(supabase\)/);
    assert.match(page, /return <ForbiddenPage \/>/);
    assert.match(middleware, /\.in\("role", \["owner", "admin"\]\)/);
    assert.match(authContext, /canAccessAdmin\(context\.role\)/);
  });

  it("blocks researcher and viewer through the shared role model", () => {
    const permissions = read("src/lib/product/permissions.ts");
    const researcherBlock = permissions.match(/researcher: \[[^\]]+\]/s)?.[0] ?? "";
    const viewerBlock = permissions.match(/viewer: \[[^\]]+\]/s)?.[0] ?? "";

    assert.doesNotMatch(researcherBlock, /admin:read|admin:manage_users/);
    assert.doesNotMatch(viewerBlock, /admin:read|admin:manage_users/);
  });

  it("scopes admin data to the active organization", () => {
    const page = read("src/app/admin/page.tsx");

    for (const table of ["product_memberships", "product_projects", "product_feedback", "product_usage_events"]) {
      assert.match(page, new RegExp(`\\.from\\("${table}"\\)`));
    }

    assert.match(page, /const organizationId = context\.organization\.id/);
    assert.match(page, /\.eq\("organization_id", organizationId\)/);
    assert.match(page, /profiles\.get\(membership\.user_id\)/);
    assert.doesNotMatch(page, /adminSummary|pilotUser|mock-data/);
  });

  it("shows required admin sections without secrets or internal workflow details", () => {
    const page = read("src/app/admin/page.tsx");

    for (const text of [
      "organization.name",
      "Members list",
      "Projects",
      "Usage events",
      "Feature flags",
      "Feedback",
      "Invite user",
      "Manage roles",
      "Billing",
      "V0.5",
    ]) {
      assert.match(page, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }

    assert.doesNotMatch(page, /secret|SUPABASE_SERVICE_ROLE_KEY|service_role|engine|raw internal|transcript/i);
  });
});
