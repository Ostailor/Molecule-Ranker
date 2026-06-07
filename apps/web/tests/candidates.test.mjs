import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("candidate pages", () => {
  it("candidate table renders required columns, filters, and sort controls", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/candidates/page.tsx");
    const explorer = read("src/components/candidates/candidate-explorer.tsx");

    assert.match(page, /CandidateExplorer/);
    for (const text of [
      "Candidate table",
      "Rank",
      "Name",
      "Modality",
      "Score",
      "Confidence",
      "Evidence count",
      "Flags/warnings",
      "Saved status mock",
      "Open detail",
      "Warning present",
      "Generated-related",
      "Saved",
      "Prioritization score",
    ]) {
      assert.match(explorer, new RegExp(text));
    }
  });

  it("candidate detail renders summary, identifiers, score, evidence, provenance, warnings, and notes", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/candidates/[candidateId]/page.tsx");
    const detail = read("src/components/candidates/candidate-detail-view.tsx");

    assert.match(page, /CandidateDetailView/);
    for (const text of [
      "Summary",
      "Identifiers placeholder",
      "Prioritization score",
      "Evidence list preview",
      "Provenance preview",
      "Warnings/limitations",
      "Notes placeholder",
      "Export candidate placeholder",
    ]) {
      assert.match(detail, new RegExp(text));
    }
  });

  it("save mock works locally without persistence or backend calls", () => {
    const explorer = read("src/components/candidates/candidate-explorer.tsx");
    const detail = read("src/components/candidates/candidate-detail-view.tsx");

    assert.match(explorer, /setSavedIds/);
    assert.match(explorer, /Saved mock/);
    assert.match(explorer, /Save mock/);
    assert.match(detail, /useState\(candidate\.status === "Saved for discussion"\)/);
    assert.match(detail, /Saved locally/);
    assert.match(detail, /setSaved\(\(current\) => !current\)/);
    assert.doesNotMatch(`${explorer}\n${detail}`, /\bfetch\s*\(/);
    assert.doesNotMatch(`${explorer}\n${detail}`, /localStorage|sessionStorage|document\.cookie/);
  });

  it("uses product-safe wording and avoids forbidden candidate claims", () => {
    const source = [
      read("src/app/projects/[projectId]/runs/[runId]/candidates/page.tsx"),
      read("src/app/projects/[projectId]/runs/[runId]/candidates/[candidateId]/page.tsx"),
      read("src/components/candidates/candidate-explorer.tsx"),
      read("src/components/candidates/candidate-detail-view.tsx"),
    ].join("\n");

    assert.match(source, /prioritization score/i);
    assert.match(source, /research review/i);
    assert.match(source, /warnings\/limitations/i);

    for (const phrase of ["efficacy score", "recommended treatment", "safety assessment"]) {
      assert.doesNotMatch(source, new RegExp(phrase, "i"));
    }
  });
});
