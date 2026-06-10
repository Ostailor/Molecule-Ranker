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
    assert.match(page, /requireUser\(`\/login\?next=\/projects\/\$\{projectId\}\/runs\/new`\)/);
    assert.match(page, /\.from\("product_projects"\)/);
    assert.match(page, /\.eq\("id", projectId\)/);

    for (const text of [
      "Disease or goal",
      "Optional target focus",
      "Workflow mode",
      "Dry run preview",
      "Mocked discovery workflow",
      "Prepare result bundle export",
      "Max generated hypotheses",
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
    assert.match(form, /Acknowledge the research-use boundary before starting a run/);
    assert.match(form, /disabled=\{!acknowledged \|\| usageBlocked \|\| status === "submitting"/);
    assert.match(form, /research-planning artifacts and hypotheses only/);
  });

  it("respects the mock feature flag for generated hypotheses", () => {
    const form = read("src/components/runs/start-discovery-run-form.tsx");
    const flags = read("src/lib/product/feature-flags.ts");

    assert.match(flags, /generatedHypothesesViewer: true/);
    assert.match(form, /productFeatureFlags\.generatedHypothesesViewer \?/);
    assert.match(form, /productFeatureFlags\.discoveryRunsPlaceholder/);
    assert.match(form, /productFeatureFlags\.exportsPlaceholder/);
    assert.match(form, /Include generated hypotheses/);
    assert.match(form, /Generated hypotheses are hidden by the current mock feature flag/);
    assert.match(form, /maxGeneratedHypothesisLimit = 3/);
    assert.match(form, /generatedCount = includeGenerated && productFeatureFlags\.generatedHypothesesViewer \? maxGeneratedHypotheses : 0/);
  });

  it("posts a mocked run through the V0.3 API and redirects to the run status page", () => {
    const page = read("src/app/projects/[projectId]/runs/new/page.tsx");
    const form = read("src/components/runs/start-discovery-run-form.tsx");

    assert.match(page, /\[89ab\]\[0-9a-f\]\{3\}-\[0-9a-f\]\{12\}/);
    assert.match(page, /checkUsageAllowed\("run_discovery", 1, \{ supabaseClient: supabase \}\)/);
    assert.match(page, /allowReadOnlyLive=\{booleanFromEnv\(process\.env\.PRODUCT_READ_ONLY_LIVE_RUNS_ENABLED\)\}/);
    assert.match(form, /fetch\(`\/api\/product\/projects\/\$\{projectId\}\/runs`/);
    assert.match(form, /disease_or_goal: String\(formData\.get\("disease_or_goal"\)/);
    assert.match(form, /target_focus: String\(formData\.get\("target_focus"\)/);
    assert.match(form, /mode: String\(formData\.get\("workflow_mode"\) \?\? "dry_run"\)/);
    assert.match(form, /include_generated_hypotheses:/);
    assert.match(form, /max_generated_hypotheses: Number\(formData\.get\("max_generated_hypotheses"\)/);
    assert.match(form, /router\.push\(`\/projects\/\$\{projectId\}\/runs\/\$\{runId\}`\)/);
    assert.match(form, /Discovery run created\. Redirecting to run status/);
    assert.doesNotMatch(form, /localStorage|sessionStorage|document\.cookie/);
  });

  it("keeps unsafe options out of the form and shows safe API errors", () => {
    const form = read("src/components/runs/start-discovery-run-form.tsx");

    assert.doesNotMatch(form, /name=".*antibody/i);
    assert.doesNotMatch(form, /name=".*external.*write/i);
    assert.doesNotMatch(form, /name=".*write.*approved/i);
    assert.doesNotMatch(form, /value="write_approved_live"/);
    assert.match(form, /allowReadOnlyLive \? \(/);
    assert.match(form, /value="read_only_live"/);
    assert.match(form, /payload\?\.error\?\.message \?\? "Could not start the discovery run\."/);
    assert.doesNotMatch(form, /error\.stack|JSON\.stringify\(error\)|console\.error/);
  });

  it("includes required disclaimers and avoids unsafe product claims", () => {
    const form = read("src/components/runs/start-discovery-run-form.tsx");
    const expected = [
      "No patient-specific info.",
      "No medical advice.",
      "Not a lab protocol.",
      "Generated hypotheses are computational only.",
      "Result bundle is not clinical validation.",
      "No synthesis instructions.",
      "No dosing.",
      "Antibody generation disabled.",
      "External writes disabled.",
      "Write-approved mode disabled.",
    ];

    for (const text of expected) {
      assert.match(form, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }

    const forbidden = ["cures", "validated drug", "safe and effective", "active binder", "clinical proof"];
    for (const phrase of forbidden) {
      assert.doesNotMatch(form, new RegExp(`\\b${phrase.replaceAll(" ", "\\s+")}\\b`, "i"));
    }
  });
});
