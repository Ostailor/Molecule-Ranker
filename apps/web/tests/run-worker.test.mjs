import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { describe, it } from "node:test";
import ts from "typescript";

const root = new URL("..", import.meta.url).pathname;
const require = createRequire(import.meta.url);

function loadTsModule(relativePath, stubs = {}, cache = new Map()) {
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
    if (request in stubs) return stubs[request];
    if (request.startsWith("./")) {
      return loadTsModule(path.join(path.dirname(relativePath), `${request}.ts`), stubs, cache);
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

function createFakeSupabase(initialRun) {
  let currentRun = { ...initialRun };
  const updates = [];

  return {
    updates,
    currentRun() {
      return currentRun;
    },
    from(table) {
      assert.equal(table, "product_runs");

      return {
        update(patch) {
          updates.push(patch);
          currentRun = { ...currentRun, ...patch };

          return {
            eq() {
              return this;
            },
            select() {
              return this;
            },
            async single() {
              return { data: currentRun, error: null };
            },
          };
        },
      };
    },
  };
}

function context(overrides = {}) {
  return {
    user: { id: "user-a" },
    organization: { id: "org-a" },
    role: "researcher",
    ...overrides,
  };
}

function project(overrides = {}) {
  return {
    id: "project-a",
    organization_id: "org-a",
    name: "Kinase discovery",
    disease_focus: "Inflammation",
    target_focus: "Target A",
    ...overrides,
  };
}

function run(overrides = {}) {
  return {
    id: "run-a",
    organization_id: "org-a",
    project_id: "project-a",
    created_by_user_id: "user-a",
    run_type: "dry_run_discovery",
    mode: "dry_run",
    status: "queued",
    disease_or_goal: "Find bounded discovery candidates",
    target_focus: "Target A",
    options: {},
    progress: { step: "queued" },
    result_summary: {},
    error_summary: null,
    started_at: null,
    completed_at: null,
    created_at: "2026-06-10T00:00:00.000Z",
    updated_at: "2026-06-10T00:00:00.000Z",
    ...overrides,
  };
}

function options(overrides = {}) {
  return {
    mode: "dry_run",
    includeGeneratedHypotheses: false,
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

function resultBundle(overrides = {}) {
  const sections = ["Run configuration", "Candidate ranking summary", "Evidence coverage summary", "Limitations"];

  return {
    artifactType: "result_bundle_json",
    displayName: "Kinase discovery result bundle",
    summary: {
      status: "Ready for review",
      sections,
      candidateCount: 4,
      evidenceItemCount: 6,
      generatedHypothesisCount: 0,
      warningCount: 2,
      mode: "dry_run",
    },
    payload: {
      product_safe: true,
      project: {
        id: "project-a",
        name: "Kinase discovery",
        disease_focus: "Inflammation",
        target_focus: "Target A",
      },
      run: {
        id: "run-a",
        disease_or_goal: "Find bounded discovery candidates",
        mode: "dry_run",
      },
      sections,
      guardrails: ["Research-planning artifact only."],
      limitations: ["V0.3 summary-level result only."],
      counts: {
        ranked_candidates: 4,
        evidence_items: 6,
        generated_hypotheses: 0,
        warnings: 2,
      },
    },
    ...overrides,
  };
}

function artifact(overrides = {}) {
  return {
    id: "artifact-a",
    organization_id: "org-a",
    project_id: "project-a",
    run_id: "run-a",
    artifact_type: "result_bundle_json",
    storage_kind: "database",
    storage_path: null,
    content_json: {},
    content_text: null,
    sha256: "abc",
    size_bytes: 12,
    public_to_user: true,
    admin_only: false,
    created_at: "2026-06-10T00:00:00.000Z",
    metadata: {},
    ...overrides,
  };
}

describe("V0.3 product run worker", () => {
  it("processes a queued mocked run, stores artifacts, and sets terminal success", async () => {
    const { executeProductRunWorker } = loadTsModule("src/lib/product/run-worker.ts");
    const fakeRun = run({ mode: "mocked", run_type: "mocked_discovery" });
    const supabase = createFakeSupabase(fakeRun);
    const storedArtifact = artifact();
    let artifactStoreInput = null;

    const result = await executeProductRunWorker({
      context: context(),
      supabase,
      project: project(),
      run: fakeRun,
      options: options({ mode: "mocked" }),
      dependencies: {
        now: () => "2026-06-10T00:00:01.000Z",
        timeoutMs: 1000,
        runner: async ({ run: runningRun }) => {
          assert.equal(runningRun.status, "running");
          return resultBundle({ summary: { ...resultBundle().summary, mode: "mocked" } });
        },
        artifactStore: async (input) => {
          artifactStoreInput = input;
          return storedArtifact;
        },
      },
    });

    assert.equal(result.executed, true);
    assert.equal(result.terminalStatus, "succeeded");
    assert.equal(result.run.status, "succeeded");
    assert.equal(result.artifact, storedArtifact);
    assert.deepEqual(
      supabase.updates.map((update) => update.status),
      ["running", "succeeded"],
    );
    assert.equal(supabase.updates[0].progress.step, "running");
    assert.equal(supabase.updates[1].progress.step, "completed");
    assert.equal(supabase.updates[1].result_summary.candidateCount, 4);
    assert.equal(artifactStoreInput.context.organization.id, "org-a");
    assert.equal(artifactStoreInput.run.id, "run-a");
  });

  it("records a safe error summary when the runner fails", async () => {
    const { executeProductRunWorker } = loadTsModule("src/lib/product/run-worker.ts");
    const fakeRun = run();
    const supabase = createFakeSupabase(fakeRun);

    const result = await executeProductRunWorker({
      context: context(),
      supabase,
      project: project(),
      run: fakeRun,
      options: options(),
      dependencies: {
        now: () => "2026-06-10T00:00:02.000Z",
        timeoutMs: 1000,
        runner: async () => {
          throw new Error("raw engine failure token=super-secret");
        },
      },
    });

    assert.equal(result.terminalStatus, "failed");
    assert.equal(result.run.status, "failed");
    assert.equal(result.run.error_summary, "The bounded discovery workflow could not prepare a product-safe result bundle.");
    assert.equal(result.run.completed_at, "2026-06-10T00:00:02.000Z");
    assert.deepEqual(
      supabase.updates.map((update) => update.status),
      ["running", "failed"],
    );
    assert.doesNotMatch(result.run.error_summary, /super-secret|raw engine failure/);
  });

  it("marks partial success when artifact storage fails after result creation", async () => {
    const { executeProductRunWorker } = loadTsModule("src/lib/product/run-worker.ts");
    const fakeRun = run();
    const supabase = createFakeSupabase(fakeRun);

    const result = await executeProductRunWorker({
      context: context(),
      supabase,
      project: project(),
      run: fakeRun,
      options: options(),
      dependencies: {
        now: () => "2026-06-10T00:00:03.000Z",
        timeoutMs: 1000,
        runner: async () => resultBundle(),
        artifactStore: async () => {
          throw new Error("database insert failed");
        },
      },
    });

    assert.equal(result.terminalStatus, "partially_succeeded");
    assert.equal(result.run.status, "partially_succeeded");
    assert.equal(result.artifact, null);
    assert.equal(result.run.result_summary.candidateCount, 4);
    assert.match(result.run.error_summary, /artifact storage failed/);
    assert.deepEqual(
      supabase.updates.map((update) => update.status),
      ["running", "partially_succeeded"],
    );
  });

  it("can be disabled by env without changing queued state", async () => {
    const { executeProductRunWorker } = loadTsModule("src/lib/product/run-worker.ts");
    const fakeRun = run();
    const supabase = createFakeSupabase(fakeRun);
    const previous = process.env.PRODUCT_RUN_WORKER_DISABLED;

    try {
      process.env.PRODUCT_RUN_WORKER_DISABLED = "true";
      const result = await executeProductRunWorker({
        context: context(),
        supabase,
        project: project(),
        run: fakeRun,
        options: options(),
        dependencies: {
          runner: async () => {
            throw new Error("runner should not execute");
          },
        },
      });

      assert.equal(result.executed, false);
      assert.equal(result.terminalStatus, "queued");
      assert.equal(result.run.status, "queued");
      assert.deepEqual(supabase.updates, []);
    } finally {
      if (previous === undefined) delete process.env.PRODUCT_RUN_WORKER_DISABLED;
      else process.env.PRODUCT_RUN_WORKER_DISABLED = previous;
    }
  });
});
