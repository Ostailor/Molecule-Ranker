import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("result bundle overview", () => {
  it("result overview renders the requested sections and cards", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/result/page.tsx");
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(page, /ResultBundleOverview/);
    for (const text of [
      "Result summary",
      "Candidate ranking summary",
      "Evidence coverage",
      "Generated hypotheses summary",
      "Key limitations",
      "Guardrail notices",
      "Export actions placeholder",
      "Human review checklist",
      "Ranked candidates",
      "Evidence items",
      "Generated hypotheses",
      "Warnings",
      "Export availability",
    ]) {
      assert.match(overview, new RegExp(text));
    }
  });

  it("links to detail sections and the export placeholder", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /\/candidates`/);
    assert.match(overview, /\/evidence`/);
    assert.match(overview, /\/generated`/);
    assert.match(overview, /href="#export-actions"/);
    assert.match(overview, /Export placeholder/);
  });

  it("limitations are visible", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /All rows are synthetic UI demo data/);
    assert.match(overview, /Candidate prioritization scores are placeholders/);
    assert.match(overview, /Evidence coverage may be incomplete/);
    assert.match(overview, /Generated hypotheses are not source-backed/);
  });

  it("generated hypothesis warning is visible", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /Generated hypotheses have no direct evidence unless exact imported results exist\./);
    assert.match(overview, /No direct evidence/);
    assert.match(overview, /Generated hypotheses are separated from evidence-backed sections/);
  });
});
