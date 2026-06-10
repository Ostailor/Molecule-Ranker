import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
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

function artifact(overrides = {}) {
  return {
    id: "artifact-a",
    organization_id: "org-a",
    project_id: "project-a",
    run_id: "run-a",
    artifact_type: "result_bundle_json",
    storage_kind: "database",
    storage_path: null,
    content_json: { ok: true },
    content_text: null,
    sha256: null,
    size_bytes: 11,
    public_to_user: true,
    admin_only: false,
    created_at: "2026-06-10T00:00:00Z",
    metadata: {},
    ...overrides,
  };
}

function context(overrides = {}) {
  return {
    organization: { id: "org-a" },
    role: "researcher",
    projectId: "project-a",
    runId: "run-a",
    ...overrides,
  };
}

describe("product artifact abstraction", () => {
  it("allows public artifacts for members in the same org/project/run", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");

    assert.equal(artifacts.artifactVisibilityFilter(context(), artifact()), true);
  });

  it("blocks admin-only artifacts for researchers and viewers", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");
    const adminArtifact = artifact({ public_to_user: false, admin_only: true, artifact_type: "trace_redacted_json" });

    assert.equal(artifacts.artifactVisibilityFilter(context({ role: "researcher" }), adminArtifact), false);
    assert.equal(artifacts.artifactVisibilityFilter(context({ role: "viewer" }), adminArtifact), false);
    assert.equal(artifacts.artifactVisibilityFilter(context({ role: "admin" }), adminArtifact), true);
    assert.equal(artifacts.artifactVisibilityFilter(context({ role: "owner" }), adminArtifact), true);
  });

  it("blocks cross-org artifacts", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");

    assert.equal(artifacts.artifactVisibilityFilter(context({ organization: { id: "org-b" } }), artifact()), false);
  });

  it("rejects oversized artifacts", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");
    const previousLimit = process.env.PRODUCT_MAX_ARTIFACT_BYTES;

    try {
      process.env.PRODUCT_MAX_ARTIFACT_BYTES = "8";
      assert.throws(
        () =>
          artifacts.validateArtifactForProductExposure({
            artifactType: "result_bundle_json",
            contentJson: { larger: "than-limit" },
            publicToUser: true,
          }),
        /configured product artifact size limit/,
      );
    } finally {
      if (previousLimit === undefined) delete process.env.PRODUCT_MAX_ARTIFACT_BYTES;
      else process.env.PRODUCT_MAX_ARTIFACT_BYTES = previousLimit;
    }
  });

  it("rejects unsafe artifact types and paths", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");

    assert.throws(
      () => artifacts.validateArtifactForProductExposure({ artifactType: "raw_tool_log", contentText: "raw log" }),
      /not product-safe/,
    );
    assert.throws(
      () => artifacts.validateArtifactForProductExposure({ artifactType: "result_bundle_json", storagePath: "../.env" }),
      /traverse/,
    );
    assert.throws(
      () =>
        artifacts.validateArtifactForProductExposure({
          artifactType: "result_bundle_json",
          storagePath: "cache/result_bundle.json",
        }),
      /Cache files/,
    );
  });

  it("computes artifact sha and size", () => {
    const artifacts = loadTsModule("src/lib/product/artifacts.ts");
    const sha = artifacts.computeArtifactSha256("product-safe artifact");

    assert.equal(sha.length, 64);
    assert.equal(artifacts.estimateArtifactSize("abc"), 3);
    assert.match(artifacts.validateArtifactForProductExposure({ artifactType: "result_bundle_json", contentText: "abc" }).sha256, /^[a-f0-9]{64}$/);
  });

  it("collects allowed engine artifacts with product artifact types, sha, and size", async () => {
    const filter = loadTsModule("src/lib/product/artifact-filter.ts");
    const outputDirectory = await mkdtemp(path.join(tmpdir(), "molcreate-artifacts-"));

    try {
      await writeFile(path.join(outputDirectory, "v3_result_bundle.json"), JSON.stringify({ sections: ["Summary"], candidateCount: 2 }), "utf8");
      await writeFile(path.join(outputDirectory, "v3_result_bundle.md"), "# Summary\n", "utf8");
      await writeFile(path.join(outputDirectory, "candidates_summary.json"), JSON.stringify({ count: 2 }), "utf8");
      await writeFile(path.join(outputDirectory, "generated_hypotheses_summary.json"), JSON.stringify({ count: 1 }), "utf8");
      await writeFile(path.join(outputDirectory, "evidence_summary.json"), JSON.stringify({ count: 3 }), "utf8");
      await writeFile(path.join(outputDirectory, "validation_summary.json"), JSON.stringify({ status: "passed" }), "utf8");
      await writeFile(path.join(outputDirectory, "product_run_input.json"), "{}", "utf8");

      const artifacts = await filter.collectEngineArtifactsFromDirectory(outputDirectory, { runSucceeded: true });
      const artifactTypes = artifacts.map((item) => item.artifactType);

      assert.deepEqual([...artifactTypes].sort(), [
        "candidates_json",
        "evidence_json",
        "generated_candidates_json",
        "result_bundle_json",
        "result_bundle_markdown",
        "validation_json",
      ].sort());
      assert.ok(artifacts.every((item) => item.sha256.length === 64));
      assert.ok(artifacts.every((item) => item.sizeBytes > 0));
      assert.ok(artifacts.every((item) => item.publicToUser));
    } finally {
      await rm(outputDirectory, { recursive: true, force: true });
    }
  });

  it("rejects blocked raw engine artifacts and unknown files", async () => {
    const filter = loadTsModule("src/lib/product/artifact-filter.ts");
    const blockedDirectory = await mkdtemp(path.join(tmpdir(), "molcreate-blocked-artifacts-"));
    const unknownDirectory = await mkdtemp(path.join(tmpdir(), "molcreate-unknown-artifacts-"));

    try {
      await mkdir(path.join(blockedDirectory, "logs"));
      await writeFile(path.join(blockedDirectory, "logs", "stdout.log"), "raw stdout token=secret", "utf8");
      await assert.rejects(
        filter.collectEngineArtifactsFromDirectory(blockedDirectory),
        /blocked from product artifact storage/,
      );

      await writeFile(path.join(unknownDirectory, "engine_notes.json"), JSON.stringify({ raw: true }), "utf8");
      await assert.rejects(
        filter.collectEngineArtifactsFromDirectory(unknownDirectory),
        /Unknown engine artifact is not product-safe/,
      );
    } finally {
      await rm(blockedDirectory, { recursive: true, force: true });
      await rm(unknownDirectory, { recursive: true, force: true });
    }
  });

  it("marks redacted diagnostics as admin-only and redacts secret-like values", async () => {
    const filter = loadTsModule("src/lib/product/artifact-filter.ts");
    const outputDirectory = await mkdtemp(path.join(tmpdir(), "molcreate-admin-artifacts-"));

    try {
      await writeFile(
        path.join(outputDirectory, "engine_diagnostics_redacted.json"),
        JSON.stringify({ message: "api_key=super-secret-token authorization: bearer hidden-token" }),
        "utf8",
      );

      const [artifact] = await filter.collectEngineArtifactsFromDirectory(outputDirectory);

      assert.equal(artifact.artifactType, "engine_diagnostics_redacted_json");
      assert.equal(artifact.adminOnly, true);
      assert.equal(artifact.publicToUser, false);
      assert.match(artifact.contentText, /\[redacted\]/);
      assert.doesNotMatch(artifact.contentText, /super-secret-token|hidden-token/);
    } finally {
      await rm(outputDirectory, { recursive: true, force: true });
    }
  });

  it("creates a fallback result bundle when a succeeded run has only summaries", async () => {
    const filter = loadTsModule("src/lib/product/artifact-filter.ts");
    const outputDirectory = await mkdtemp(path.join(tmpdir(), "molcreate-fallback-artifacts-"));

    try {
      await writeFile(path.join(outputDirectory, "candidates_summary.json"), JSON.stringify({ count: 4 }), "utf8");
      await writeFile(path.join(outputDirectory, "evidence_summary.json"), JSON.stringify({ count: 6 }), "utf8");
      await writeFile(path.join(outputDirectory, "validation_summary.json"), JSON.stringify({ status: "passed", warningCount: 1 }), "utf8");

      const artifacts = await filter.collectEngineArtifactsFromDirectory(outputDirectory, { runSucceeded: true });
      const fallback = artifacts.find((item) => item.metadata.fallback === true);

      assert.equal(fallback.artifactType, "result_bundle_json");
      assert.equal(fallback.publicToUser, true);
      assert.equal(fallback.contentJson.fallback, true);
      assert.equal(fallback.contentJson.candidateCount, 4);
      assert.equal(fallback.contentJson.evidenceItemCount, 6);
    } finally {
      await rm(outputDirectory, { recursive: true, force: true });
    }
  });
});
