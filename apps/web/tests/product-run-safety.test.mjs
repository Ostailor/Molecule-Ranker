import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path, { join } from "node:path";
import { describe, it } from "node:test";
import ts from "typescript";

const root = new URL("..", import.meta.url).pathname;
const require = createRequire(import.meta.url);

function read(relativePath) {
  return readFileSync(join(root, relativePath), "utf8");
}

function loadTsModule(relativePath, cache = new Map()) {
  const absolutePath = path.join(root, relativePath);
  if (cache.has(absolutePath)) return cache.get(absolutePath).exports;

  const source = readFileSync(absolutePath, "utf8");
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

function baseRunInput(overrides = {}) {
  return {
    acknowledgement: true,
    mode: "dry_run",
    disease_or_goal: "ExampleDiseaseA planning run",
    target_focus: "ExampleTargetA",
    ...overrides,
  };
}

function productRun(overrides = {}) {
  return {
    id: "run-a",
    organization_id: "org-a",
    project_id: "project-a",
    mode: "dry_run",
    disease_or_goal: "ExampleDiseaseA planning run",
    target_focus: "ExampleTargetA",
    options: {},
    ...overrides,
  };
}

function safeOptions(overrides = {}) {
  return {
    mode: "dry_run",
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

describe("product run safety", () => {
  it("POST run rejects external write mode", () => {
    const runSafety = loadTsModule("src/lib/product/run-safety.ts");
    const route = read("src/app/api/product/projects/[projectId]/runs/route.ts");

    assert.throws(() => runSafety.readSafeRunOptions(baseRunInput({ mode: "write_approved_live" })), /Choose mocked, dry_run/);
    assert.throws(() => runSafety.readSafeRunOptions(baseRunInput({ external_writes: true })), /External writes/);
    assert.match(route, /readSafeRunOptions\(input\)/);
    assert.match(route, /const options = readSafeRunOptions\(input\)[\s\S]*?\.from\("product_runs"\)[\s\S]*?\.insert\(/);
  });

  it("POST run rejects antibody generation", () => {
    const runSafety = loadTsModule("src/lib/product/run-safety.ts");

    assert.throws(
      () => runSafety.readSafeRunOptions(baseRunInput({ enable_antibody_generation: true })),
      /Antibody generation is disabled/,
    );
    assert.throws(
      () => runSafety.readSafeRunOptions(baseRunInput({ antibody_generation: "true" })),
      /Antibody generation is disabled/,
    );
  });

  it("POST run rejects unsafe free text asking for dosing, protocol, or synthesis", () => {
    const runSafety = loadTsModule("src/lib/product/run-safety.ts");

    for (const disease_or_goal of [
      "Create a dosing plan for ExampleDiseaseA.",
      "Write a lab protocol for ExampleDiseaseA.",
      "Explain synthesis steps for ExampleCandidateA.",
    ]) {
      assert.throws(() => runSafety.readSafeRunOptions(baseRunInput({ disease_or_goal })), /Do not request treatment/);
    }
  });

  it("engine command builder does not use a shell string", () => {
    const previousWorkdir = process.env.PRODUCT_RUN_WORKDIR;

    try {
      process.env.PRODUCT_RUN_WORKDIR = "/tmp/molcreate-product-runs";
      const engineRunner = loadTsModule("src/lib/product/engine-runner.ts");
      const command = engineRunner.buildEngineRunCommand(productRun(), safeOptions());
      const engineRunnerSource = read("src/lib/product/engine-runner.ts");

      assert.equal(command.command.includes(" "), false);
      assert.match(engineRunnerSource, /shell: false/);
      assert.doesNotMatch(engineRunnerSource, /shell:\s*true/);
    } finally {
      if (previousWorkdir === undefined) delete process.env.PRODUCT_RUN_WORKDIR;
      else process.env.PRODUCT_RUN_WORKDIR = previousWorkdir;
    }
  });

  it("engine args are array-based", () => {
    const previousWorkdir = process.env.PRODUCT_RUN_WORKDIR;

    try {
      process.env.PRODUCT_RUN_WORKDIR = "/tmp/molcreate-product-runs";
      const engineRunner = loadTsModule("src/lib/product/engine-runner.ts");
      const command = engineRunner.buildEngineRunCommand(productRun(), safeOptions());

      assert.ok(Array.isArray(command.args));
      assert.ok(command.args.length > 0);
      assert.equal(command.args.includes("--no-external-writes"), true);
      assert.equal(command.args.includes("--no-antibody-generation"), true);
    } finally {
      if (previousWorkdir === undefined) delete process.env.PRODUCT_RUN_WORKDIR;
      else process.env.PRODUCT_RUN_WORKDIR = previousWorkdir;
    }
  });

  it("working directory is isolated under PRODUCT_RUN_WORKDIR", () => {
    const previousWorkdir = process.env.PRODUCT_RUN_WORKDIR;
    const rootDir = "/tmp/molcreate-product-runs";

    try {
      process.env.PRODUCT_RUN_WORKDIR = rootDir;
      const engineRunner = loadTsModule("src/lib/product/engine-runner.ts");
      const command = engineRunner.buildEngineRunCommand(
        productRun({ organization_id: "org-a", project_id: "project-a", id: "run-a" }),
        safeOptions(),
      );

      assert.equal(command.cwd, path.join(rootDir, "org_org-a", "project_project-a", "run_run-a"));
      assert.equal(command.outputDirectory, command.cwd);
      assert.equal(command.inputPath, path.join(command.cwd, "product_run_input.json"));
      assert.ok(command.cwd.startsWith(path.resolve(rootDir)));
    } finally {
      if (previousWorkdir === undefined) delete process.env.PRODUCT_RUN_WORKDIR;
      else process.env.PRODUCT_RUN_WORKDIR = previousWorkdir;
    }
  });

  it("raw logs are not user-facing", () => {
    const statusRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/status/route.ts");
    const resultRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/result-bundle/route.ts");
    const runSummary = read("src/components/runs/run-summary.tsx");

    assert.match(statusRoute, /status, progress, error_summary, result_summary/);
    assert.doesNotMatch(statusRoute + resultRoute + runSummary, /stdout|stderr|raw logs|raw engine trace|raw Codex transcript/i);
  });

  it("raw Codex transcript artifact is blocked", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");

    assert.throws(
      () => artifacts.validateArtifactForProductExposure({ artifactType: "raw_codex_transcript", contentText: "raw transcript" }),
      /not product-safe/,
    );
  });

  it("failed run returns safe error summary", () => {
    const { ProductRunExecutionError, safeEngineError } = loadTsModule("src/lib/product/run-errors.ts");
    const worker = read("src/lib/product/run-worker.ts");
    const safe = safeEngineError(new ProductRunExecutionError("failed", "stderr token=super-secret stack trace"));

    assert.equal(safe.publicMessage, "The bounded discovery workflow could not prepare a product-safe result bundle.");
    assert.doesNotMatch(safe.publicMessage, /stderr|super-secret|stack trace/);
    assert.match(worker, /error_summary: safeError\.publicMessage/);
  });

  it("result bundle disclaimer exists", async () => {
    const mockRunner = loadTsModule("src/lib/product/mock-engine-runner.ts");
    const result = await mockRunner.runMockProductSafeDiscoveryWorkflow({
      project: { id: "project-a", organization_id: "org-a", name: "ExampleProjectA", disease_focus: null, target_focus: null },
      run: productRun(),
      options: safeOptions(),
    });
    const combined = `${result.payload.guardrails.join("\n")}\n${result.payload.limitations.join("\n")}`;

    assert.match(combined, /Synthetic UI-test artifact only/);
    assert.match(combined, /Not medical advice/);
    assert.match(combined, /Human review is required/);
  });

  it("generated hypotheses have no direct evidence", () => {
    const mockRunner = loadTsModule("src/lib/product/mock-engine-runner.ts");
    const artifacts = mockRunner.createMockEngineArtifacts({
      run: productRun(),
      options: safeOptions({ maxGeneratedHypotheses: 3 }),
    });
    const generated = artifacts.find((artifact) => artifact.artifactType === "generated_candidates_json");

    assert.ok(generated);
    assert.equal(generated.contentJson.count, 3);
    assert.ok(generated.contentJson.hypotheses.every((hypothesis) => hypothesis.direct_evidence === false));
  });

  it("cross-org run and artifact access is blocked", () => {
    const runRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/route.ts");
    const statusRoute = read("src/app/api/product/projects/[projectId]/runs/[runId]/status/route.ts");
    const artifactStorage = loadTsModule("src/lib/product/artifacts.ts");

    assert.match(runRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.match(statusRoute, /\.eq\("organization_id", context\.organization\.id\)/);
    assert.equal(
      artifactStorage.artifactVisibilityFilter(
        { organization: { id: "org-a" }, role: "researcher", projectId: "project-a", runId: "run-a" },
        {
          id: "artifact-b",
          organization_id: "org-b",
          project_id: "project-b",
          run_id: "run-b",
          public_to_user: true,
          admin_only: false,
        },
      ),
      false,
    );
  });

  it("viewer cannot create run", () => {
    const permissions = read("src/lib/product/permissions.ts");
    const route = read("src/app/api/product/projects/[projectId]/runs/route.ts");

    assert.match(route, /requireProductPermission\("run:create", supabase\)/);
    assert.doesNotMatch(permissions.match(/viewer: \[[^\]]+\]/s)?.[0] ?? "", /run:create/);
  });

  it("usage limit blocks run creation", () => {
    const route = read("src/app/api/product/projects/[projectId]/runs/route.ts");
    const usage = read("src/lib/product/usage.ts");
    const checkIndex = route.indexOf("await checkRunUsageLimits({ context, supabase, options });");
    const insertIndex = route.indexOf('.from("product_runs")', checkIndex);

    assert.ok(checkIndex > -1);
    assert.ok(insertIndex > -1);
    assert.ok(checkIndex < insertIndex);
    assert.match(route, /checkUsageAllowed\("run_discovery", 1, \{ context, supabaseClient: supabase \}\)/);
    assert.match(usage, /throw productApiError\("PLAN_LIMIT_EXCEEDED"/);
  });
});
