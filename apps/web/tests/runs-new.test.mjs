import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("new discovery run page", () => {
  it("page renders the bounded discovery workflow surface", () => {
    const page = read("src/app/projects/[projectId]/runs/new/page.tsx");
    const form = read("src/components/runs/start-discovery-run-form.tsx");

    assert.match(page, /Start discovery run/);
    assert.match(page, /StartDiscoveryRunForm/);

    for (const text of [
      "Disease / project objective",
      "Optional target focus",
      "Workflow mode",
      "Dry run preview",
      "Read-only evidence workflow placeholder",
      "Prepare result bundle export",
      "Usage estimate",
      "1 discovery run",
      "Estimated Codex task usage",
    ]) {
      assert.match(form, new RegExp(text));
    }
  });

  it("requires the research-use acknowledgement in the UI", () => {
    const form = read("src/components/runs/start-discovery-run-form.tsx");

    assert.match(form, /name="research-use-acknowledgement"/);
    assert.match(form, /\brequired\b/);
    assert.match(form, /Acknowledgement required/);
    assert.match(form, /disabled=\{!acknowledged\}/);
    assert.match(form, /research-planning artifacts and hypotheses only/);
  });

  it("respects the mock feature flag for generated hypotheses", () => {
    const form = read("src/components/runs/start-discovery-run-form.tsx");
    const flags = read("src/lib/feature-flags.ts");

    assert.match(flags, /generationPreview: true/);
    assert.match(form, /featureFlags\.generationPreview \?/);
    assert.match(form, /Include generated hypotheses/);
    assert.match(form, /Generated hypotheses are hidden by the current mock feature flag/);
    assert.match(form, /generatedCount = includeGenerated && featureFlags\.generationPreview \? 3 : 0/);
  });

  it("creates only local mock state and links to the mock run page", () => {
    const page = read("src/app/projects/[projectId]/runs/new/page.tsx");
    const form = read("src/components/runs/start-discovery-run-form.tsx");

    assert.match(page, /const runHref = `\/projects\/\$\{projectId\}\/runs\/\$\{runId\}`/);
    assert.match(form, /setMockStarted\(true\)/);
    assert.match(form, /Open mock run/);
    assert.match(form, /No backend execution was started/);
    assert.doesNotMatch(form, /\bfetch\s*\(/);
    assert.doesNotMatch(form, /localStorage|sessionStorage|document\.cookie/);
    assert.doesNotMatch(page, /\bfetch\s*\(/);
  });

  it("includes required disclaimers and avoids unsafe product claims", () => {
    const form = read("src/components/runs/start-discovery-run-form.tsx");
    const expected = [
      "Generated molecules are computational hypotheses.",
      "Result bundle is not clinical validation.",
      "No medical advice.",
      "No lab protocols.",
      "No synthesis instructions.",
      "No dosing.",
    ];

    for (const text of expected) {
      assert.match(form, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }

    const forbidden = ["cures", "validated drug", "safe", "effective", "active binder", "clinical proof"];
    for (const phrase of forbidden) {
      assert.doesNotMatch(form, new RegExp(`\\b${phrase.replaceAll(" ", "\\s+")}\\b`, "i"));
    }
  });
});
