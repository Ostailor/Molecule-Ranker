import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("support and admin pages", () => {
  it("usage page renders plan, usage, storage, exports, and billing placeholders", () => {
    const page = read("src/app/usage/page.tsx");

    for (const text of [
      "Plan placeholder",
      "Projects used",
      "Runs used",
      "Generated hypotheses usage",
      "Exports usage",
      "Storage usage",
      "Billing placeholder",
      "Release V0.5",
    ]) {
      assert.match(page, new RegExp(text));
    }
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

  it("admin page is clearly placeholder and admin-only", () => {
    const page = read("src/app/admin/page.tsx");

    for (const text of [
      "Admin-only placeholder",
      "not available to normal users",
      "Pilot users summary mock",
      "Projects/runs mock",
      "Feature flags mock",
      "Support status mock",
      "PLACEHOLDER_V0_1_ADMIN",
    ]) {
      assert.match(page, new RegExp(text));
    }
  });
});
