import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;
const mockSource = readFileSync(join(root, "src/lib/mock-data.ts"), "utf8");

function exportBlock(name) {
  const match = mockSource.match(new RegExp(`export const ${name}[\\s\\S]*?;\\n`, "m"));
  assert.ok(match, `Expected ${name} export to exist`);
  return match[0];
}

describe("synthetic mock data", () => {
  it("defines the shared UI-demo metadata marker", () => {
    assert.match(mockSource, /export const uiDemoMetadata = \{\s*synthetic: true,\s*for_ui_demo_only: true,\s*\} as const;/);
  });

  it("marks exported mock records as synthetic UI-demo data", () => {
    for (const name of ["pilotUser", "organization", "usageSummary", "adminSummary"]) {
      assert.match(exportBlock(name), /metadata: uiDemoMetadata/);
    }

    for (const name of ["projects", "runs", "resultBundles", "candidates", "evidenceItems", "generatedHypotheses", "feedbackMessages"]) {
      const block = exportBlock(name);
      const recordCount = block.match(/\bid:/g)?.length ?? 0;
      const metadataCount = block.match(/metadata: uiDemoMetadata/g)?.length ?? 0;

      assert.ok(recordCount > 0, `Expected ${name} to include records`);
      assert.equal(metadataCount, recordCount, `Every ${name} record must include metadata`);
    }
  });

  it("marks every generated hypothesis as having no direct evidence", () => {
    const block = exportBlock("generatedHypotheses");
    const recordCount = block.match(/\bid:/g)?.length ?? 0;
    const noDirectEvidenceCount = block.match(/noDirectEvidence: true/g)?.length ?? 0;

    assert.ok(recordCount > 0);
    assert.equal(noDirectEvidenceCount, recordCount);
  });

  it("does not include fake real biomedical identifiers", () => {
    const forbiddenPatterns = [
      /\bPMID[:\s-]?\d+/i,
      /\bDOI[:\s-]?10\.\d{4,9}\//i,
      /\b10\.\d{4,9}\/[-._;()/:A-Z0-9]+/i,
      /\bCHEMBL\d+\b/i,
      /\bPubChem\b/i,
      /\bCID\s*[:#-]?\s*\d+\b/i,
      /\bLRRK2\b/i,
      /\bGBA1\b/i,
      /\bSNCA\b/i,
      /\bPINK1\b/i,
      /\bNeurodegeneration\b/i,
      /\bOncology\b/i,
      /\bImmunology\b/i,
    ];

    for (const pattern of forbiddenPatterns) {
      assert.doesNotMatch(mockSource, pattern);
    }
  });
});

