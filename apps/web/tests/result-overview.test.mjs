import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("result bundle overview", () => {
  it("fetches and renders result bundle summary from sample API data", () => {
    const page = read("src/app/projects/[projectId]/runs/[runId]/result/page.tsx");
    const overview = read("src/components/runs/result-bundle-overview.tsx");
    const api = read("src/app/api/product/projects/[projectId]/runs/[runId]/result-bundle/route.ts");

    assert.match(page, /ResultBundleOverview/);
    assert.match(page, /initialRun=\{run\}/);
    assert.doesNotMatch(page, /\.from\("product_run_artifacts"\)/);
    assert.match(overview, /"use client"/);
    assert.match(overview, /fetch\(`\/api\/product\/projects\/\$\{projectId\}\/runs\/\$\{runId\}\/result-bundle`/);
    assert.match(api, /artifacts/);
    assert.match(api, /artifact_type === "result_bundle_json"/);

    for (const text of [
      "Result summary",
      "Candidate summary",
      "Generated summary",
      "Evidence summary",
      "Limitations",
      "Required human review",
      "Artifact list",
      "Ranked candidates",
      "Evidence items",
      "Generated hypotheses",
      "Warnings",
      "Artifacts",
    ]) {
      assert.match(overview, new RegExp(text));
    }
  });

  it("does not build deep candidate or evidence viewers in V0.3", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /Deep candidate and evidence viewers remain a V0\.4 scope item/);
    assert.match(overview, /Deep candidate inspection remains V0\.4 scope/);
    assert.match(overview, /Deep evidence review remains V0\.4 scope/);
    assert.doesNotMatch(overview, /\/candidates`|\/evidence`|\/generated`/);
    assert.doesNotMatch(overview, /Open synthetic|Export disabled|href="#export-actions"/);
  });

  it("shows pending, failed, and partial states safely", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /Result bundle pending/);
    assert.match(overview, /Loading result bundle/);
    assert.match(overview, /The product-safe result bundle is not available yet/);
    assert.match(overview, /Run failed before result bundle creation/);
    assert.match(overview, /The bounded workflow could not prepare a product-safe result bundle/);
    assert.match(overview, /Partial result warning/);
    assert.match(overview, /This run partially succeeded/);
    assert.match(overview, /response\.status === 401 \|\| response\.status === 403 \|\| response\.status === 404/);
    assert.doesNotMatch(overview, /error\.stack|JSON\.stringify\(error\)|console\.error|stderr|stdout/i);
  });

  it("limitations are visible", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /bounded product-safe summary/);
    assert.match(overview, /Deep candidate and evidence viewers remain a V0\.4 scope item/);
    assert.match(overview, /Evidence coverage may be incomplete/);
    assert.match(overview, /Generated hypotheses are computational only/);
    assert.match(overview, /Required human review/);
  });

  it("generated hypothesis warning is visible", () => {
    const overview = read("src/components/runs/result-bundle-overview.tsx");

    assert.match(overview, /Generated hypotheses are computational only and require separate human review/);
    assert.match(overview, /Computational only/);
    assert.match(overview, /Generated hypotheses require human review and are not direct evidence/);
  });
});
