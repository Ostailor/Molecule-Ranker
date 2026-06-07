import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("V0.1 placeholder pages", () => {
  it("login page renders mock-only access controls and acknowledgement text", () => {
    const loginPage = read("src/app/login/page.tsx");

    assert.match(loginPage, /PLACEHOLDER_V0_1_AUTH/);
    assert.match(loginPage, /type="email"/);
    assert.match(loginPage, /I acknowledge MolCreate is for research-planning artifacts and hypotheses only/);
    assert.match(loginPage, /Pilot access note/);
    assert.match(loginPage, /Research-use disclaimer/);
    assert.match(loginPage, /Terms placeholder/);
    assert.match(loginPage, /Privacy placeholder/);
    assert.match(loginPage, /disabled/);
    assert.match(read("src/app/terms-placeholder/page.tsx"), /PLACEHOLDER_V0_1_TERMS/);
    assert.match(read("src/app/privacy-placeholder/page.tsx"), /PLACEHOLDER_V0_1_PRIVACY/);
  });

  it("login page has no real credential submission path", () => {
    const loginPage = read("src/app/login/page.tsx");

    assert.doesNotMatch(loginPage, /\baction=/);
    assert.doesNotMatch(loginPage, /type="submit"/);
    assert.doesNotMatch(loginPage, /\bfetch\s*\(/);
    assert.doesNotMatch(loginPage, /localStorage|sessionStorage|document\.cookie/);
  });

  it("onboarding page renders the V0.1 placeholder checklist and cards", () => {
    const onboardingPage = read("src/app/onboarding/page.tsx");
    const expected = [
      "Confirm research-use boundary",
      "Create first project",
      "Run first discovery workflow",
      "Review result bundle",
      "Export/save candidates",
      "Mock organization",
      "Usage-limit preview",
      "Support contact",
      "Release V0.2",
      "PLACEHOLDER_V0_1_ONBOARDING",
    ];

    for (const text of expected) {
      assert.match(onboardingPage, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }
  });

  it("onboarding page does not store user data or call a backend", () => {
    const onboardingPage = read("src/app/onboarding/page.tsx");

    assert.doesNotMatch(onboardingPage, /\bfetch\s*\(/);
    assert.doesNotMatch(onboardingPage, /localStorage|sessionStorage|document\.cookie/);
  });
});
