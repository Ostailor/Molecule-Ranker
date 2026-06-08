import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("route protection", () => {
  it("redirects unauthenticated users away from protected routes", () => {
    const middleware = read("src/lib/supabase/middleware.ts");

    for (const route of ["/onboarding", "/dashboard", "/projects", "/usage", "/account", "/feedback", "/admin"]) {
      assert.match(middleware, new RegExp(`"${route}"`));
    }

    assert.match(middleware, /isRouteMatch\(pathname, protectedRoutePrefixes\) && !claims/);
    assert.match(middleware, /redirectToLogin\(request, pathname\)/);
    assert.match(middleware, /redirectUrl\.pathname = "\/login"/);
    assert.match(middleware, /redirectUrl\.searchParams\.set\("next"/);
  });

  it("redirects authenticated users from login to dashboard", () => {
    const middleware = read("src/lib/supabase/middleware.ts");

    assert.match(middleware, /const authRedirectRoutePrefixes = \["\/login"\]/);
    assert.match(middleware, /claims && isRouteMatch\(pathname, authRedirectRoutePrefixes\)/);
    assert.match(middleware, /redirectToDashboard\(request\)/);
    assert.match(middleware, /redirectUrl\.pathname = "\/dashboard"/);
  });

  it("redirects authenticated users without onboarding except account and logout", () => {
    const middleware = read("src/lib/supabase/middleware.ts");

    for (const route of ["/account", "/logout", "/onboarding"]) {
      assert.match(middleware, new RegExp(`"${route}"`));
    }

    assert.match(middleware, /select\("onboarding_completed"\)/);
    assert.match(middleware, /!profile\?\.onboarding_completed/);
    assert.match(middleware, /redirectToOnboarding\(request, pathname\)/);
    assert.match(middleware, /redirectUrl\.pathname = "\/onboarding"/);
  });

  it("blocks researcher and viewer access to admin with a safe 403 page", () => {
    const middleware = read("src/lib/supabase/middleware.ts");
    const forbiddenRoute = read("src/app/forbidden/page.tsx");
    const forbiddenPage = read("src/components/auth/forbidden-page.tsx");

    assert.match(middleware, /const adminRoutePrefixes = \["\/admin"\]/);
    assert.match(middleware, /\.from\("product_memberships"\)/);
    assert.match(middleware, /\.eq\("status", "active"\)/);
    assert.match(middleware, /\.in\("role", \["owner", "admin"\]\)/);
    assert.match(middleware, /rewriteToForbidden\(request\)/);
    assert.match(middleware, /status: 403/);
    assert.match(forbiddenRoute, /ForbiddenPage/);
    assert.match(forbiddenPage, /Access unavailable/);
    assert.match(forbiddenPage, /limited to workspace owners and admins/);
    assert.doesNotMatch(forbiddenPage, /Pilot users summary mock|Projects\/runs mock|Feature flags mock/);
  });

  it("allows owner and admin roles to reach the admin page", () => {
    const middleware = read("src/lib/supabase/middleware.ts");
    const adminPage = read("src/app/admin/page.tsx");

    assert.match(middleware, /role === "owner" \|\| role === "admin"/);
    assert.match(middleware, /\.in\("role", \["owner", "admin"\]\)/);
    assert.match(middleware, /if \(!isAdminRole\(adminMembership\?\.role\)\) \{/);
    assert.match(adminPage, /requireAdminRole\(supabase\)/);
    assert.match(adminPage, /Members list/);
  });
});
