import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { describe, it } from "node:test";
import ts from "typescript";

const root = new URL("..", import.meta.url).pathname;
const require = createRequire(import.meta.url);

function loadTsModule(relativePath, stubs = {}, cache = new Map()) {
  const absolutePath = path.join(root, relativePath);
  if (cache.has(absolutePath)) return cache.get(absolutePath).exports;

  const source = awaitableRead(absolutePath);
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
    return require(request);
  }

  const execute = new Function("require", "module", "exports", compiled);
  execute(localRequire, module, module.exports);
  return module.exports;
}

function awaitableRead(filePath) {
  return require("node:fs").readFileSync(filePath, "utf8");
}

function fakeChildProcessSpawn(calls) {
  return function spawn(command, args, options) {
    calls.push({ command, args, options });

    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => {
      child.killed = true;
      return true;
    };

    queueMicrotask(async () => {
      const outputDirectory = args[args.indexOf("--output-dir") + 1];
      await writeFile(
        path.join(outputDirectory, "result_bundle.json"),
        JSON.stringify({
          sections: ["Candidate ranking summary", "Evidence coverage summary"],
          candidateCount: 2,
          evidenceItemCount: 3,
          generatedHypothesisCount: 1,
          warningCount: 1,
        }),
        "utf8",
      );
      await writeFile(path.join(outputDirectory, "validation_summary.json"), JSON.stringify({ status: "passed" }), "utf8");
      child.stdout.write("engine complete api_key=super-secret-token\n");
      child.stderr.write("authorization: bearer hidden-token\n");
      child.stdout.end();
      child.stderr.end();
      child.emit("close", 0);
    });

    return child;
  };
}

describe("product-safe engine runner", () => {
  it("executes with mocked child_process spawn and redacts diagnostics", async () => {
    const workdir = await mkdtemp(path.join(tmpdir(), "molcreate-runs-"));
    const previousEnv = { ...process.env };
    const calls = [];

    try {
      process.env.PRODUCT_ENABLE_ENGINE_RUNNER = "true";
      process.env.PRODUCT_RUN_WORKDIR = workdir;
      process.env.PRODUCT_ENGINE_COMMAND = "molecule-ranker-test";
      process.env.PRODUCT_RUN_TIMEOUT_SECONDS = "5";
      process.env.PRODUCT_MAX_ARTIFACT_BYTES = "100000";

      const runner = loadTsModule("src/lib/product/engine-runner.ts", {
        "node:child_process": { spawn: fakeChildProcessSpawn(calls) },
      });
      const run = {
        id: "run-1",
        organization_id: "org-1",
        project_id: "project-1",
        disease_or_goal: "Bounded discovery objective",
        target_focus: "Target A",
        mode: "dry_run",
        options: {},
      };
      const options = {
        mode: "dry_run",
        includeGeneratedHypotheses: true,
        prepareResultBundle: true,
        externalWritesEnabled: false,
        writeApprovedLiveEnabled: false,
        antibodyGenerationEnabled: false,
        externalIntegrationsEnabled: false,
        exposeRawEngineInternals: false,
        exposeRawCodexTranscript: false,
        exposeRawTraceLogs: false,
      };

      const result = await runner.executeEngineRun(run, options, fakeChildProcessSpawn(calls));

      assert.equal(calls.length, 1);
      assert.equal(calls[0].command, "molecule-ranker-test");
      assert.deepEqual(calls[0].args.slice(0, 5), ["discover", "--mode", "dry_run", "--input-json", path.join(workdir, "org_org-1", "project_project-1", "run_run-1", "product_run_input.json")]);
      assert.equal(calls[0].options.shell, false);
      assert.equal(calls[0].options.cwd, path.join(workdir, "org_org-1", "project_project-1", "run_run-1"));
      assert.equal(result.status, "succeeded");
      assert.equal(result.summary.candidateCount, 2);
      assert.ok(result.artifacts.some((artifact) => artifact.artifactType === "result_bundle_json" && artifact.publicToUser));
      assert.ok(result.artifacts.some((artifact) => artifact.artifactType === "trace_redacted_json" && artifact.adminOnly));
      assert.match(result.diagnostics, /api_key=\[redacted\]/);
      assert.doesNotMatch(result.diagnostics, /super-secret-token|hidden-token/);

      const input = JSON.parse(await readFile(calls[0].args[calls[0].args.indexOf("--input-json") + 1], "utf8"));
      assert.equal(input.external_writes, false);
      assert.equal(input.enable_antibody_generation, false);
    } finally {
      process.env = previousEnv;
      await rm(workdir, { recursive: true, force: true });
    }
  });

  it("rejects unsafe engine options before spawning", async () => {
    const workdir = await mkdtemp(path.join(tmpdir(), "molcreate-runs-"));
    const previousEnv = { ...process.env };
    const calls = [];

    try {
      process.env.PRODUCT_ENABLE_ENGINE_RUNNER = "true";
      process.env.PRODUCT_RUN_WORKDIR = workdir;
      const runner = loadTsModule("src/lib/product/engine-runner.ts", {
        "node:child_process": { spawn: fakeChildProcessSpawn(calls) },
      });
      const run = {
        id: "run-unsafe",
        organization_id: "org-1",
        project_id: "project-1",
        disease_or_goal: "Bounded discovery objective",
        target_focus: null,
        mode: "dry_run",
        options: {},
      };

      await assert.rejects(
        runner.executeEngineRun(run, {
          mode: "dry_run",
          includeGeneratedHypotheses: false,
          prepareResultBundle: true,
          externalWritesEnabled: true,
          writeApprovedLiveEnabled: false,
          antibodyGenerationEnabled: false,
          externalIntegrationsEnabled: false,
          exposeRawEngineInternals: false,
          exposeRawCodexTranscript: false,
          exposeRawTraceLogs: false,
        }),
        /Unsafe engine run options are disabled/,
      );
      assert.equal(calls.length, 0);
    } finally {
      process.env = previousEnv;
      await rm(workdir, { recursive: true, force: true });
    }
  });
});
