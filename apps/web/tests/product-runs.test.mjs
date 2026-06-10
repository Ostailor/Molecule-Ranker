import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

describe("product run types and helpers", () => {
  it("defines V0.3 product run domain types", () => {
    const types = read("src/lib/product/types.ts");
    const runs = read("src/lib/product/runs.ts");

    assert.ok(existsSync(join(root, "src/lib/product/runs.ts")));

    for (const typeName of [
      "ProductRun",
      "ProductRunStatus",
      "ProductRunMode",
      "ProductRunArtifact",
      "ProductRunOptions",
      "ProductRunProgress",
      "ProductResultSummary",
    ]) {
      assert.match(types, new RegExp(`export type ${typeName}\\b`));
    }

    for (const helper of [
      "isTerminalRunStatus",
      "isSuccessfulRunStatus",
      "runStatusLabel",
      "runModeLabel",
      "safeRunOptions",
      "validateRunOptions",
    ]) {
      assert.match(runs, new RegExp(`export function ${helper}\\b`));
    }
  });

  it("defaults run options to V0.3 safe boundaries", () => {
    const types = read("src/lib/product/types.ts");
    const runs = read("src/lib/product/runs.ts");

    for (const option of [
      "enableGeneration: boolean",
      "maxGeneratedHypotheses: number",
      "enableBiologics: boolean",
      "enableAntibodyGeneration: boolean",
      "enableStructure: boolean",
      "enableCodexSummary: boolean",
      "externalWrites: false",
      'mode: ProductRunMode',
    ]) {
      assert.match(types, new RegExp(option.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    }

    assert.match(types, /"mocked" \| "dry_run" \| "read_only_live"/);
    assert.match(runs, /export const safeMaxGeneratedHypotheses = 3/);
    assert.match(runs, /PRODUCT_DISCOVERY_RUN_MODE === "mocked"/);
    assert.match(runs, /enableGeneration: flags\.generatedHypothesesViewer && requestedGeneration/);
    assert.match(runs, /enableBiologics: false/);
    assert.match(runs, /enableAntibodyGeneration: false/);
    assert.match(runs, /enableStructure: false/);
    assert.match(runs, /enableCodexSummary: isCodexRuntimeEnabled\(\) && input\.enableCodexSummary !== false/);
    assert.match(runs, /externalWrites: false/);
  });

  it("rejects unsafe run options", () => {
    const runs = read("src/lib/product/runs.ts");

    assert.match(runs, /Unsupported discovery run mode/);
    assert.match(runs, /read_only_live mode is disabled for V0\.3/);
    assert.match(runs, /Generated hypothesis limit must be between 0 and/);
    assert.match(runs, /Generated hypotheses are disabled by product configuration/);
    assert.match(runs, /Structure workflows are disabled for V0\.3 discovery runs/);
    assert.match(runs, /Biologics workflows are disabled for V0\.3 discovery runs/);
  });

  it("keeps antibody generation and external writes unavailable", () => {
    const runs = read("src/lib/product/runs.ts");

    assert.match(runs, /if \(input\.enableAntibodyGeneration === true\)/);
    assert.match(runs, /Antibody generation is disabled for discovery runs/);
    assert.match(runs, /if \(input\.externalWrites === true \|\| input\.externalIntegrations === true \|\| input\.writeApprovedLive === true\)/);
    assert.match(runs, /External writes and integrations are unavailable for discovery runs/);
  });

  it("run status and mode helpers cover all persisted statuses", () => {
    const runs = read("src/lib/product/runs.ts");

    for (const status of ["queued", "running", "succeeded", "failed", "partially_succeeded", "cancelled"]) {
      assert.match(runs, new RegExp(`case "${status}"`));
    }

    assert.match(runs, /const terminalRunStatuses = \["succeeded", "failed", "partially_succeeded", "cancelled"\]/);
    assert.match(runs, /const successfulRunStatuses = \["succeeded", "partially_succeeded"\]/);

    for (const mode of ["mocked", "dry_run", "read_only_live"]) {
      assert.match(runs, new RegExp(`case "${mode}"`));
    }
  });
});
