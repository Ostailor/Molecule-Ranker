import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("evidence page", () => {
  it("evidence page renders evidence list fields and filters", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/evidence/page.tsx");
    const explorer = read("src/components/evidence/evidence-explorer.tsx");

    assert.match(page, /EvidenceExplorer/);
    for (const text of [
      "Evidence item list",
      "Source type",
      "Related candidate",
      "Provenance placeholder",
      "Limitations",
      "Candidate",
      "Confidence",
      "Warning",
      "review source provenance",
      "independently verify evidence",
      "lack of evidence is not evidence of absence",
    ]) {
      assert.match(explorer, new RegExp(text));
    }
  });

  it("synthetic mock warning is visible", () => {
    const explorer = read("src/components/evidence/evidence-explorer.tsx");

    assert.match(
      explorer,
      /This V0\.1 page uses synthetic UI data\. Real source-backed evidence will be connected in a later release\./,
    );
    assert.match(explorer, /Synthetic evidence notice/);
    assert.match(explorer, /Synthetic UI data only/);
  });

  it("does not show fake real biomedical identifiers", () => {
    const source = `${read("src/components/evidence/evidence-explorer.tsx")}\n${read("src/lib/mock-data.ts")}`;
    const forbiddenPatterns = [
      /\bPMID[:\s-]?\d+/i,
      /\bPubMed\b/i,
      /\bDOI[:\s-]?10\.\d{4,9}\//i,
      /\b10\.\d{4,9}\/[-._;()/:A-Z0-9]+/i,
      /\bCHEMBL\d+\b/i,
      /\bChEMBL\b/i,
      /\bPubChem\b/i,
      /\bCID\s*[:#-]?\s*\d+\b/i,
    ];

    for (const pattern of forbiddenPatterns) {
      assert.doesNotMatch(source, pattern);
    }
  });
});
