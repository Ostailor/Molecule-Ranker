import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;
const srcDir = join(root, "src");

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

function sourceFiles(dir = srcDir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(path) ? [path] : [];
  });
}

describe("Supabase auth utilities", () => {
  it("defines browser, server, middleware, auth, and type utilities", () => {
    for (const relativePath of [
      "src/lib/supabase/client.ts",
      "src/lib/supabase/server.ts",
      "src/lib/supabase/middleware.ts",
      "src/lib/supabase/auth.ts",
      "src/lib/supabase/types.ts",
      "src/middleware.ts",
    ]) {
      assert.ok(statSync(join(root, relativePath)).isFile(), `${relativePath} should exist`);
    }
  });

  it("creates browser and server clients from publishable environment variables", () => {
    const client = read("src/lib/supabase/client.ts");
    const server = read("src/lib/supabase/server.ts");
    const types = read("src/lib/supabase/types.ts");

    assert.match(client, /createBrowserClient<Database>/);
    assert.match(server, /createServerClient<Database>/);
    assert.match(server, /cookies\(\)/);
    assert.match(server, /getAll\(\)/);
    assert.match(server, /setAll\(cookiesToSet\)/);
    assert.match(types, /NEXT_PUBLIC_SUPABASE_URL/);
    assert.match(types, /NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY/);
    assert.match(types, /Missing required Supabase environment variable/);
  });

  it("refreshes sessions and protects app routes through Next middleware", () => {
    const middleware = read("src/lib/supabase/middleware.ts");
    const entrypoint = read("src/middleware.ts");

    assert.match(middleware, /supabase\.auth\.getClaims\(\)/);
    assert.match(middleware, /protectedRoutePrefixes/);
    assert.match(middleware, /adminRoutePrefixes/);
    assert.match(middleware, /authRedirectRoutePrefixes/);
    assert.match(middleware, /onboardingExemptRoutePrefixes/);
    assert.match(middleware, /pathname = "\/login"/);
    assert.match(middleware, /pathname = "\/onboarding"/);
    assert.match(middleware, /NextResponse\.rewrite\(rewriteUrl, \{ status: 403 \}\)/);
    assert.match(entrypoint, /updateSession\(request\)/);
  });

  it("exposes safe server-side auth helpers", () => {
    const auth = read("src/lib/supabase/auth.ts");

    for (const helper of ["getCurrentUser", "getCurrentProfile", "requireUser", "signOut", "getSessionClaims"]) {
      assert.match(auth, new RegExp(`export async function ${helper}\\b`));
    }

    assert.match(auth, /supabase\.auth\.getUser\(\)/);
    assert.match(auth, /supabase\.auth\.getClaims\(\)/);
    assert.match(auth, /supabase\.from\("product_profiles"\)/);
    assert.match(auth, /supabase\.auth\.signOut\(\)/);
  });

  it("does not reference the service-role key from web source", () => {
    const findings = sourceFiles()
      .map((file) => [file, readFileSync(file, "utf8")])
      .filter(([, source]) => source.includes("SUPABASE_SERVICE_ROLE_KEY"))
      .map(([file]) => relative(root, file));

    assert.deepEqual(findings, []);
  });
});
