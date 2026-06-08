import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("product API route stubs", () => {
  it("defines the requested V0.2 product API routes", () => {
    for (const route of [
      "src/app/api/product/me/route.ts",
      "src/app/api/product/projects/route.ts",
      "src/app/api/product/projects/[projectId]/route.ts",
      "src/app/api/product/usage/route.ts",
      "src/app/api/product/feedback/route.ts",
      "src/app/api/product/admin/summary/route.ts",
    ]) {
      assert.ok(existsSync(join(root, route)), `${route} should exist`);
    }
  });

  it("requires auth for all current product API endpoints and returns sanitized failures", () => {
    const routeSources = [
      read("src/app/api/product/me/route.ts"),
      read("src/app/api/product/projects/route.ts"),
      read("src/app/api/product/projects/[projectId]/route.ts"),
      read("src/app/api/product/usage/route.ts"),
      read("src/app/api/product/feedback/route.ts"),
      read("src/app/api/product/admin/summary/route.ts"),
    ].join("\n");
    const apiErrors = read("src/lib/product/api-errors.ts");

    assert.match(routeSources, /getProductAuthContext|requireProductPermission|requireOrganizationMember|requireAdminRole/);
    assert.match(routeSources, /sanitizeProductApiError\(error\)/);
    assert.match(routeSources, /failure\(apiError\.code, apiError\.publicMessage, apiError\.status\)/);
    assert.match(apiErrors, /UNAUTHENTICATED: 401/);
    assert.match(apiErrors, /FORBIDDEN: 403/);
    assert.doesNotMatch(routeSources, /error\.message|JSON\.stringify\(error\)|stack|service_role|SUPABASE_SERVICE_ROLE_KEY/);
  });

  it("me endpoint returns sanitized auth context", () => {
    const route = read("src/app/api/product/me/route.ts");

    for (const field of ["user", "profile", "organization", "membership", "role", "plan"]) {
      assert.match(route, new RegExp(`\\b${field}\\b`));
    }

    assert.match(route, /id: context\.user\.id/);
    assert.match(route, /email: context\.user\.email \?\? null/);
    assert.doesNotMatch(route, /identities|sessions|factors|app_metadata|user_metadata/);
  });

  it("projects endpoints are scoped to the active organization", () => {
    const listRoute = read("src/app/api/product/projects/route.ts");
    const detailRoute = read("src/app/api/product/projects/[projectId]/route.ts");

    assert.match(listRoute, /requireProductPermission\("project:read", supabase\)/);
    assert.match(listRoute, /requireProductPermission\("project:create", supabase\)/);
    assert.match(listRoute, /\.from\("product_projects"\)/);
    assert.match(listRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(listRoute, /organization_id: context\.organization\.id/);
    assert.match(listRoute, /created_by_user_id: context\.user\.id/);
    assert.match(listRoute, /checkUsageAllowed\("create_project", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(listRoute, /recordUsageEvent\("create_project", 1, \{ project_id: data\.id \}/);
    assert.match(detailRoute, /\.eq\("id", projectId\)/);
    assert.match(detailRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(detailRoute, /throw productApiError\("NOT_FOUND"\)/);
  });

  it("usage and feedback endpoints use organization membership boundaries", () => {
    const usageRoute = read("src/app/api/product/usage/route.ts");
    const feedbackRoute = read("src/app/api/product/feedback/route.ts");

    assert.match(usageRoute, /requireOrganizationMember\(supabase\)/);
    assert.match(usageRoute, /getUsageSummaryForOrg\(context\.organization\.id, \{ context, supabaseClient: supabase \}\)/);
    assert.match(feedbackRoute, /requireProductPermission\("feedback:create", supabase\)/);
    assert.match(feedbackRoute, /\.from\("product_feedback"\)/);
    assert.match(feedbackRoute, /organization_id: context\.organization\.id/);
    assert.match(feedbackRoute, /user_id: context\.user\.id/);
    assert.match(feedbackRoute, /checkUsageAllowed\("feedback_create", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(feedbackRoute, /recordUsageEvent\("feedback_create", 1/);
    assert.match(feedbackRoute, /VALIDATION_ERROR/);
  });

  it("admin summary requires owner or admin role and blocks researchers", () => {
    const adminRoute = read("src/app/api/product/admin/summary/route.ts");
    const authContext = read("src/lib/product/auth-context.ts");
    const permissions = read("src/lib/product/permissions.ts");

    assert.match(adminRoute, /requireAdminRole\(supabase\)/);
    assert.match(authContext, /canAccessAdmin\(context\.role\)/);
    assert.match(permissions, /researcher: \[/);
    assert.doesNotMatch(permissions.match(/researcher: \[[^\]]+\]/s)?.[0] ?? "", /admin:read/);
    assert.match(adminRoute, /\.from\("product_projects"\)/);
    assert.match(adminRoute, /\.from\("product_memberships"\)/);
    assert.match(adminRoute, /\.from\("product_feedback"\)/);
    assert.match(adminRoute, /\.from\("product_usage_events"\)/);
  });

  it("does not expose raw workflow internals or V0.3 API surfaces", () => {
    const routeSources = [
      read("src/app/api/product/me/route.ts"),
      read("src/app/api/product/projects/route.ts"),
      read("src/app/api/product/projects/[projectId]/route.ts"),
      read("src/app/api/product/usage/route.ts"),
      read("src/app/api/product/feedback/route.ts"),
      read("src/app/api/product/admin/summary/route.ts"),
    ].join("\n");

    assert.doesNotMatch(routeSources, /engine|raw internal|codex|transcript/i);
    assert.ok(!existsSync(join(root, "src/app/api/product/runs/route.ts")));
    assert.ok(!existsSync(join(root, "src/app/api/product/candidates/route.ts")));
    assert.ok(!existsSync(join(root, "src/app/api/product/evidence/route.ts")));
    assert.ok(!existsSync(join(root, "src/app/api/product/generated/route.ts")));
  });
});
