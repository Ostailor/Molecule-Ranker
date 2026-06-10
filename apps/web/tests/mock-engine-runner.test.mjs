import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { describe, it } from "node:test";
import ts from "typescript";

const root = new URL("..", import.meta.url).pathname;
const require = createRequire(import.meta.url);

function loadTsModule(relativePath, cache = new Map()) {
  const absolutePath = path.join(root, relativePath);
  if (cache.has(absolutePath)) return cache.get(absolutePath).exports;

  const source = require("node:fs").readFileSync(absolutePath, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText;
  const module = { exports: {} };
  cache.set(absolutePath, module);

  function localRequire(request) {
    if (request.startsWith("./")) {
      return loadTsModule(path.join(path.dirname(relativePath), `${request}.ts`), cache);
    }
    if (request.startsWith("@/")) {
      return {};
    }
    return require(request);
  }

  const execute = new Function("require", "module", "exports", compiled);
  execute(localRequire, module, module.exports);
  return module.exports;
}

function project(overrides = {}) {
  return {
    id: "project-a",
    organization_id: "org-a",
    name: "ExampleProjectA",
    disease_focus: null,
    target_focus: null,
    ...overrides,
  };
}

function run(overrides = {}) {
  return {
    id: "run-a",
    organization_id: "org-a",
    project_id: "project-a",
    mode: "mocked",
    disease_or_goal: "Ignored user objective",
    target_focus: "Ignored target",
    options: {},
    ...overrides,
  };
}

function options(overrides = {}) {
  return {
    mode: "mocked",
    includeGeneratedHypotheses: true,
    maxGeneratedHypotheses: 2,
    prepareResultBundle: true,
    externalWritesEnabled: false,
    writeApprovedLiveEnabled: false,
    antibodyGenerationEnabled: false,
    externalIntegrationsEnabled: false,
    exposeRawEngineInternals: false,
    exposeRawCodexTranscript: false,
    exposeRawTraceLogs: false,
    ...overrides,
  };
}

function allArtifactText(artifacts) {
  return artifacts.map((artifact) => `${artifact.contentText}\n${JSON.stringify(artifact.metadata)}`).join("\n");
}

const forbiddenIdentifierPatterns = [
  /\bPMID[:\s-]?\d+/i,
  /\bDOI[:\s-]?10\.\d{4,9}\//i,
  /\b10\.\d{4,9}\/[-._;()/:A-Z0-9]+/i,
  /\bCHEMBL\d+\b/i,
  /\bPubChem\b/i,
  /\bCID\s*[:#-]?\s*\d+\b/i,
];

describe("deterministic mock engine runner", () => {
  it("creates valid synthetic result artifacts", () => {
    const mockRunner = loadTsModule("src/lib/product/mock-engine-runner.ts");

    const artifacts = mockRunner.createMockEngineArtifacts({
      project: project(),
      run: run(),
      options: options(),
    });
    const artifactTypes = artifacts.map((artifact) => artifact.artifactType).sort();

    assert.deepEqual(artifactTypes, [
      "candidates_json",
      "evidence_json",
      "generated_candidates_json",
      "result_bundle_json",
      "result_bundle_markdown",
      "validation_json",
    ].sort());
    assert.ok(artifacts.every((artifact) => artifact.sha256.length === 64));
    assert.ok(artifacts.every((artifact) => artifact.sizeBytes > 0));
    assert.ok(artifacts.every((artifact) => artifact.publicToUser === true));
    assert.ok(artifacts.every((artifact) => artifact.adminOnly === false));
  });

  it("marks artifacts as synthetic and for UI tests only", async () => {
    const mockRunner = loadTsModule("src/lib/product/mock-engine-runner.ts");

    const artifacts = mockRunner.createMockEngineArtifacts({
      project: project(),
      run: run(),
      options: options(),
    });
    const result = await mockRunner.runMockProductSafeDiscoveryWorkflow({
      project: project(),
      run: run(),
      options: options(),
    });

    assert.ok(artifacts.every((artifact) => artifact.metadata.synthetic === true));
    assert.ok(artifacts.every((artifact) => artifact.metadata.for_ui_test_only === true));
    for (const artifact of artifacts.filter((item) => item.contentJson)) {
      assert.equal(artifact.contentJson.synthetic, true);
      assert.equal(artifact.contentJson.for_ui_test_only, true);
    }
    assert.equal(result.payload.synthetic, true);
    assert.equal(result.payload.for_ui_test_only, true);
  });

  it("does not include fake real identifiers or disease-specific biomedical claims", () => {
    const mockRunner = loadTsModule("src/lib/product/mock-engine-runner.ts");
    const artifacts = mockRunner.createMockEngineArtifacts({
      project: project({ disease_focus: "Inflammation" }),
      run: run({ disease_or_goal: "Oncology target ranking", target_focus: "LRRK2" }),
      options: options(),
    });
    const artifactText = allArtifactText(artifacts);

    for (const pattern of forbiddenIdentifierPatterns) {
      assert.doesNotMatch(artifactText, pattern);
    }
    assert.doesNotMatch(artifactText, /\bInflammation\b|\bOncology\b|\bLRRK2\b/i);
    assert.match(artifactText, /ExampleDiseaseA/);
    assert.match(artifactText, /ExampleTargetA/);
    assert.match(artifactText, /ExampleCandidateA/);
  });

  it("marks generated hypotheses as lacking direct evidence", () => {
    const mockRunner = loadTsModule("src/lib/product/mock-engine-runner.ts");
    const artifacts = mockRunner.createMockEngineArtifacts({
      project: project(),
      run: run(),
      options: options({ maxGeneratedHypotheses: 3 }),
    });
    const generated = artifacts.find((artifact) => artifact.artifactType === "generated_candidates_json");

    assert.ok(generated);
    assert.equal(generated.contentJson.count, 3);
    assert.ok(generated.contentJson.hypotheses.every((hypothesis) => hypothesis.direct_evidence === false));
  });

  it("is used by the product workflow when mock engine mode is enabled", async () => {
    const previousMode = process.env.PRODUCT_ENGINE_RUNNER_MODE;

    try {
      process.env.PRODUCT_ENGINE_RUNNER_MODE = "mock";
      const engineRunner = loadTsModule("src/lib/product/engine-runner.ts");
      const result = await engineRunner.runProductSafeDiscoveryWorkflow({
        project: project({ name: "Local demo project" }),
        run: run({ mode: "dry_run" }),
        options: options({ mode: "dry_run", includeGeneratedHypotheses: false }),
      });

      assert.equal(result.payload.synthetic, true);
      assert.equal(result.payload.project.disease_focus, "ExampleDiseaseA");
      assert.equal(result.summary.generatedHypothesisCount, 0);
    } finally {
      if (previousMode === undefined) delete process.env.PRODUCT_ENGINE_RUNNER_MODE;
      else process.env.PRODUCT_ENGINE_RUNNER_MODE = previousMode;
    }
  });
});
