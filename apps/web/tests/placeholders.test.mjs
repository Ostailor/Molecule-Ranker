import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("auth and placeholder pages", () => {
  it("login page renders real Supabase auth controls and research-use text", () => {
    const loginPage = read("src/app/login/page.tsx");
    const loginForm = read("src/components/auth/login-form.tsx");

    assert.match(loginPage, /LoginForm/);
    assert.match(loginForm, /loginAction/);
    assert.match(loginForm, /type="email"/);
    assert.match(loginForm, /type="password"/);
    assert.match(loginForm, /AuthSubmitButton/);
    assert.match(loginPage, /Research-use disclaimer/);
    assert.match(loginPage, /Terms placeholder/);
    assert.match(loginPage, /Privacy placeholder/);
    assert.match(read("src/app/terms-placeholder/page.tsx"), /PLACEHOLDER_V0_1_TERMS/);
    assert.match(read("src/app/privacy-placeholder/page.tsx"), /PLACEHOLDER_V0_1_PRIVACY/);
  });

  it("auth forms avoid client-side credential storage or fetch calls", () => {
    const source = [
      read("src/components/auth/login-form.tsx"),
      read("src/components/auth/signup-form.tsx"),
      read("src/components/auth/forgot-password-form.tsx"),
      read("src/components/auth/reset-password-form.tsx"),
      read("src/components/auth/auth-submit-button.tsx"),
    ].join("\n");

    assert.match(source, /\baction=\{formAction\}/);
    assert.match(source, /type="submit"/);
    assert.doesNotMatch(source, /\bfetch\s*\(/);
    assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie/);
  });

  it("onboarding page renders the authenticated V0.2 setup form and status", () => {
    const onboardingPage = read("src/app/onboarding/page.tsx");
    const onboardingForm = read("src/components/onboarding/onboarding-form.tsx");
    const source = `${onboardingPage}\n${onboardingForm}`;
    const expected = [
      "requireUser",
      "OnboardingForm",
      "Current account status",
      "Authenticated user",
      "researchUseAcknowledged",
      "Research-use boundary",
      "Finish onboarding",
      "product_profiles",
      "product_memberships",
      "product_organizations",
    ];

    for (const text of expected) {
      assert.match(source, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }

    for (const field of ["researchUseAcknowledged", "displayName", "organizationName", "useCase"]) {
      assert.match(onboardingForm, new RegExp(`name="${field}"`));
    }
  });

  it("onboarding avoids patient and payment collection", () => {
    const onboardingPage = read("src/app/onboarding/page.tsx");
    const onboardingForm = read("src/components/onboarding/onboarding-form.tsx");
    const source = `${onboardingPage}\n${onboardingForm}`;

    assert.match(source, /Do not enter\s+patient-specific data/);
    assert.match(source, /protected health information/);
    assert.match(source, /payment (details|information)/);
    assert.doesNotMatch(source, /medical history|diagnosis|date of birth|insurance|credit card/i);
    assert.doesNotMatch(source, /\bfetch\s*\(/);
    assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie/);
  });
});
