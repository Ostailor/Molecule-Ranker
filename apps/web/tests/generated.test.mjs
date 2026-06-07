import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("generated hypotheses page", () => {
  it("warning banner is visible", () => {
    const explorer = read("src/components/generated/generated-hypotheses-explorer.tsx");

    assert.match(
      explorer,
      /Generated hypotheses are computational structures or ideas\. They are not known actives, not validated molecules, and not evidence of safety, efficacy, binding, or therapeutic value\./,
    );
    assert.match(explorer, /Generated hypothesis warning/);
    assert.match(explorer, /Required review boundary/);
  });

  it("generated hypotheses have noDirectEvidence", () => {
    const mockData = read("src/lib/mock-data.ts");
    const explorer = read("src/components/generated/generated-hypotheses-explorer.tsx");

    assert.match(mockData, /noDirectEvidence: true/);
    assert.match(explorer, /noDirectEvidence: \{String\(hypothesis\.noDirectEvidence\)\}/);
    assert.match(explorer, /No direct evidence/);
  });

  it("feature-disabled and empty states work", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/generated/page.tsx");
    const explorer = read("src/components/generated/generated-hypotheses-explorer.tsx");

    assert.match(page, /query\?\.state !== "disabled"/);
    assert.match(page, /query\?\.state === "empty" \? \[\] : generatedHypotheses/);
    assert.match(explorer, /Generated hypotheses disabled/);
    assert.match(explorer, /No generated hypotheses/);
  });

  it("filtered-out state and required card fields render", () => {
    const explorer = read("src/components/generated/generated-hypotheses-explorer.tsx");

    for (const text of [
      "Generated hypothesis cards",
      "Parent candidate",
      "Hypothesis type",
      "Score",
      "Confidence",
      "Warnings",
      "Required human review",
      "All generated hypotheses are filtered out",
    ]) {
      assert.match(explorer, new RegExp(text));
    }
  });

  it("does not use prohibited generated-item claims", () => {
    const explorer = read("src/components/generated/generated-hypotheses-explorer.tsx");
    const forbidden = ["molecules that bind", "active binder", "safe", "developable", "experiment-ready"];

    for (const phrase of forbidden) {
      assert.doesNotMatch(explorer, new RegExp(`\\b${phrase.replaceAll(" ", "\\s+")}\\b`, "i"));
    }
  });
});
