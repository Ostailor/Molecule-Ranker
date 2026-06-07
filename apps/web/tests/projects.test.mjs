import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("project pages", () => {
  it("create project page renders the requested mock fields", () => {
    const page = read("src/app/projects/new/page.tsx");

    for (const label of ["Project name", "Research goal", "Disease or area", "Optional target focus", "Notes"]) {
      assert.match(page, new RegExp(label));
    }

    assert.match(page, /Create mock project/);
    assert.match(page, /\/dashboard\?projectCreated=1/);
    assert.match(page, /No project record is saved and no\s+backend request is made/);
  });

  it("project detail page renders summary, runs, candidates, usage, and result bundles", () => {
    const page = read("src/app/projects/[projectId]/page.tsx");

    for (const text of [
      "Project summary",
      "Recent runs",
      "Saved candidates",
      "Project runs",
      "Start new discovery run",
      "Result bundles",
      "Project not found",
      "No discovery runs yet",
    ]) {
      assert.match(page, new RegExp(text));
    }
  });

  it("unsafe-request warning appears on create project page", () => {
    const page = read("src/app/projects/new/page.tsx");

    assert.match(page, /Do not enter patient-specific or protected health information\./);
    assert.match(page, /Do not request treatment, dosing, synthesis, or lab protocols\./);
  });
});

