import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("V0.2 Supabase auth flows", () => {
  it("renders login, signup, forgot-password, and reset-password forms", () => {
    const expected = [
      ["src/app/login/page.tsx", "LoginForm"],
      ["src/app/signup/page.tsx", "SignupForm"],
      ["src/app/forgot-password/page.tsx", "ForgotPasswordForm"],
      ["src/app/reset-password/page.tsx", "ResetPasswordForm"],
    ];

    for (const [path, component] of expected) {
      assert.match(read(path), new RegExp(component));
    }
  });

  it("signup collects required pilot account fields and acknowledgement", () => {
    const signup = read("src/components/auth/signup-form.tsx");
    const page = read("src/app/signup/page.tsx");

    for (const field of ["email", "password", "displayName", "organizationName", "researchUseAcknowledged"]) {
      assert.match(signup, new RegExp(`name="${field}"`));
    }

    assert.match(signup, /I acknowledge MolCreate is for research-planning artifacts and hypotheses only/);
    assert.match(page, /Do not enter patient-specific or protected health information/);
    assert.match(page, /Do not request treatment, dosing,\s+synthesis, or lab protocols/);
  });

  it("server actions call Supabase Auth and product table setup helpers", () => {
    const actions = read("src/lib/supabase/auth-actions.ts");

    for (const call of [
      "signInWithPassword",
      "signUp",
      "resetPasswordForEmail",
      "updateUser",
      "product_profiles",
      "product_organizations",
      "product_memberships",
      "role: \"owner\"",
      "research_use_acknowledged_at",
      "redirect(\"/onboarding\")",
    ]) {
      assert.match(actions, new RegExp(call.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  });

  it("auth callback and logout routes exchange sessions and redirect safely", () => {
    const callback = read("src/app/auth/callback/route.ts");
    const logout = read("src/app/logout/route.ts");

    assert.match(callback, /exchangeCodeForSession/);
    assert.match(callback, /safeRedirectPath/);
    assert.match(callback, /auth_callback_failed/);
    assert.match(logout, /auth\.signOut\(\)/);
    assert.match(logout, /signedOut=1/);
  });

  it("auth actions do not import service role keys or social auth", () => {
    const source = [
      read("src/lib/supabase/auth-actions.ts"),
      read("src/app/auth/callback/route.ts"),
      read("src/app/logout/route.ts"),
    ].join("\n");

    assert.doesNotMatch(source, /SUPABASE_SERVICE_ROLE_KEY/);
    assert.doesNotMatch(source, /signInWithOAuth|provider:/);
  });
});
