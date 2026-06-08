import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("product API auth utilities", () => {
  it("defines auth context, standardized errors, and response helpers", () => {
    for (const relativePath of [
      "src/lib/product/auth-context.ts",
      "src/lib/product/api-errors.ts",
      "src/lib/product/api-response.ts",
    ]) {
      assert.ok(statSync(join(root, relativePath)).isFile(), `${relativePath} should exist`);
    }
  });

  it("exports the required product API auth helpers", () => {
    const authContext = read("src/lib/product/auth-context.ts");

    for (const helper of [
      "getProductAuthContext",
      "requireProductUser",
      "requireOrganizationMember",
      "requireProductPermission",
      "requireAdminRole",
      "getActiveOrganizationForUser",
    ]) {
      assert.match(authContext, new RegExp(`export async function ${helper}\\b`));
    }

    for (const field of ["user", "profile", "organization", "membership", "role", "plan"]) {
      assert.match(authContext, new RegExp(`\\b${field}\\b`));
    }
  });

  it("supports mocked Supabase client injection for route-handler tests", () => {
    const authContext = read("src/lib/product/auth-context.ts");
    const mockedSupabaseClient = {
      auth: { getUser: async () => ({ data: { user: { id: "user_1" } }, error: null }) },
      from: () => ({
        select: () => ({
          eq: () => ({
            eq: () => ({ limit: () => ({ maybeSingle: async () => ({ data: null, error: null }) }) }),
            maybeSingle: async () => ({ data: null, error: null }),
          }),
        }),
      }),
    };

    assert.ok(mockedSupabaseClient.auth);
    assert.match(authContext, /supabaseClient\?: ProductAuthSupabaseClient/);
    assert.match(authContext, /supabaseClient \?\? \(await createClient\(\)\)/);
    assert.match(authContext, /requireProductUser\(supabase\)/);
    assert.match(authContext, /getActiveOrganizationForUser\(user\.id, supabase\)/);
  });

  it("loads user, profile, active membership, and active organization without exposing service errors", () => {
    const authContext = read("src/lib/product/auth-context.ts");

    assert.match(authContext, /supabase\.auth\.getUser\(\)/);
    assert.match(authContext, /\.from\("product_profiles"\)/);
    assert.match(authContext, /\.from\("product_memberships"\)/);
    assert.match(authContext, /\.from\("product_organizations"\)/);
    assert.match(authContext, /\.eq\("user_id", userId\)/);
    assert.match(authContext, /\.eq\("status", "active"\)/);
    assert.match(authContext, /organization\.status !== "active"/);
    assert.doesNotMatch(authContext, /error\.message|JSON\.stringify\(error\)|service_role|SUPABASE_SERVICE_ROLE_KEY/);
  });

  it("enforces product permissions and admin role through the role model", () => {
    const authContext = read("src/lib/product/auth-context.ts");

    assert.match(authContext, /roleHasPermission\(context\.role, permission\)/);
    assert.match(authContext, /canAccessAdmin\(context\.role\)/);
    assert.match(authContext, /throw productApiError\("FORBIDDEN"\)/);
  });

  it("defines standardized API error codes and sanitized response helpers", () => {
    const apiErrors = read("src/lib/product/api-errors.ts");
    const apiResponse = read("src/lib/product/api-response.ts");

    for (const code of [
      "UNAUTHENTICATED",
      "ONBOARDING_REQUIRED",
      "ORGANIZATION_REQUIRED",
      "FORBIDDEN",
      "PLAN_LIMIT_EXCEEDED",
      "NOT_FOUND",
      "VALIDATION_ERROR",
    ]) {
      assert.match(apiErrors, new RegExp(`${code}: "${code}"`));
    }

    assert.match(apiResponse, /export function success<T>\(data: T/);
    assert.match(apiResponse, /export function failure\(errorCode: ProductApiErrorCode, message: string/);
    assert.match(apiResponse, /NextResponse\.json/);
    assert.match(apiResponse, /ok: true/);
    assert.match(apiResponse, /ok: false/);
    assert.match(apiResponse, /error: \{\s+code: errorCode,\s+message,/);
    assert.doesNotMatch(apiResponse, /stack|cause|details|hint|SUPABASE_SERVICE_ROLE_KEY/);
  });
});
