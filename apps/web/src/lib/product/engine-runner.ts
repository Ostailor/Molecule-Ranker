import { spawn, type ChildProcessByStdio } from "node:child_process";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type { Readable } from "node:stream";

import {
  collectEngineArtifactsFromDirectory,
  filterArtifactsForProduct,
  summarizeEngineResult,
  type EngineArtifact,
} from "./artifact-filter";
import { runMockProductSafeDiscoveryWorkflow } from "./mock-engine-runner";
import { ProductRunConfigurationError, ProductRunExecutionError, safeEngineError } from "./run-errors";
import type { ProductRunSafeOptions } from "./run-safety";
import type { Json, ProductRun, Project } from "@/lib/supabase/types";

export type ProductSafeResultBundle = {
  artifactType: "result_bundle_json";
  displayName: string;
  summary: {
    status: "Ready for review";
    sections: string[];
    candidateCount: number;
    evidenceItemCount: number;
    generatedHypothesisCount: number;
    warningCount: number;
    mode: string;
  };
  payload: {
    product_safe: true;
    project: {
      id: string;
      name: string;
      disease_focus: string | null;
      target_focus: string | null;
    };
    run: {
      id: string;
      disease_or_goal: string;
      mode: string;
    };
    sections: string[];
    guardrails: string[];
    limitations: string[];
    counts: {
      ranked_candidates: number;
      evidence_items: number;
      generated_hypotheses: number;
      warnings: number;
    };
  };
};

export type EngineRunCommand = {
  command: string;
  args: string[];
  cwd: string;
  outputDirectory: string;
  inputPath: string;
};

export type EngineRunResult = {
  status: "succeeded" | "failed";
  artifacts: EngineArtifact[];
  summary: ReturnType<typeof summarizeEngineResult>;
  diagnostics: string;
};

export type EngineRunSpawn = typeof spawn;

const guardrails = [
  "Research-planning artifact only.",
  "Not clinical validation.",
  "Not medical advice.",
  "Not a lab protocol.",
  "Not a synthesis plan.",
  "Generated hypotheses are computational only.",
  "No dosing guidance.",
  "Raw engine internals are not included.",
];

const limitations = [
  "This V0.3 workflow uses a bounded dry-run or mocked product wrapper.",
  "Candidate and evidence detail viewers remain synthetic until a later release imports product-safe artifacts.",
  "Generated hypotheses are computational only and require expert review.",
  "External writes, integrations, antibody generation, raw traces, and raw transcripts are disabled.",
];

const safeGeneratedHypothesisLimit = 3;

function booleanFromEnv(value: string | undefined) {
  return value === "1" || value === "true" || value === "TRUE";
}

function isMockEngineRunnerMode(options: ProductRunSafeOptions) {
  return process.env.PRODUCT_ENGINE_RUNNER_MODE === "mock" || process.env.NODE_ENV === "test" || options.mode === "mocked";
}

function assertSafeRunnerOptions(options: ProductRunSafeOptions) {
  if (options.mode !== "mocked" && options.mode !== "dry_run" && options.mode !== "read_only_live") {
    throw new ProductRunConfigurationError("Unsupported discovery run mode.");
  }

  if (
    options.externalWritesEnabled ||
    options.writeApprovedLiveEnabled ||
    options.externalIntegrationsEnabled ||
    options.exposeRawEngineInternals ||
    options.exposeRawCodexTranscript ||
    options.exposeRawTraceLogs
  ) {
    throw new ProductRunConfigurationError("Unsafe engine run options are disabled for V0.3.");
  }

  if (options.antibodyGenerationEnabled) {
    throw new ProductRunConfigurationError("Antibody generation is disabled for V0.3 discovery runs.");
  }
}

function safePathSegment(prefix: string, value: string) {
  const sanitized = value.replace(/[^a-zA-Z0-9_-]/g, "_");
  if (!sanitized) throw new ProductRunConfigurationError(`Invalid ${prefix} identifier for product run workdir.`);

  return `${prefix}_${sanitized}`;
}

function getRunWorkdirRoot() {
  const configuredRoot = process.env.PRODUCT_RUN_WORKDIR;
  if (!configuredRoot) {
    throw new ProductRunConfigurationError("PRODUCT_RUN_WORKDIR must be configured before enabling engine execution.");
  }

  return path.resolve(configuredRoot);
}

function getRunOutputDirectory(run: ProductRun) {
  return path.join(
    getRunWorkdirRoot(),
    safePathSegment("org", run.organization_id),
    safePathSegment("project", run.project_id),
    safePathSegment("run", run.id),
  );
}

function getTimeoutMs() {
  const seconds = Number.parseInt(process.env.PRODUCT_RUN_TIMEOUT_SECONDS ?? "", 10);
  return (Number.isFinite(seconds) && seconds > 0 ? seconds : 120) * 1000;
}

function appendBounded(current: string, chunk: Buffer, maxBytes: number) {
  const next = current + chunk.toString("utf8");
  if (new TextEncoder().encode(next).byteLength <= maxBytes) return next;

  return next.slice(-maxBytes);
}

function writeEngineInputFile(run: ProductRun, options: ProductRunSafeOptions, inputPath: string) {
  const generatedHypothesisCount = getGeneratedHypothesisCount(options);
  const input = {
    run_id: run.id,
    organization_id: run.organization_id,
    project_id: run.project_id,
    disease_or_goal: run.disease_or_goal,
    target_focus: run.target_focus,
    mode: options.mode,
    enable_generation: options.includeGeneratedHypotheses,
    max_generated_hypotheses: generatedHypothesisCount,
    enable_biologics: false,
    enable_antibody_generation: false,
    enable_structure: false,
    external_writes: false,
  };

  return fs.writeFile(inputPath, JSON.stringify(input, null, 2), { encoding: "utf8", flag: "w" });
}

export async function createRunWorkingDirectory(run: ProductRun) {
  const outputDirectory = getRunOutputDirectory(run);
  await fs.mkdir(outputDirectory, { recursive: true });

  return outputDirectory;
}

export function buildEngineRunCommand(run: ProductRun, options: ProductRunSafeOptions): EngineRunCommand {
  assertSafeRunnerOptions(options);

  const outputDirectory = getRunOutputDirectory(run);
  const inputPath = path.join(outputDirectory, "product_run_input.json");
  const command = process.env.PRODUCT_ENGINE_COMMAND || "molecule-ranker";
  const generatedHypothesisCount = getGeneratedHypothesisCount(options);
  const args = [
    "discover",
    "--mode",
    options.mode,
    "--input-json",
    inputPath,
    "--output-dir",
    outputDirectory,
    "--max-generated-hypotheses",
    String(generatedHypothesisCount),
    "--no-external-writes",
    "--no-antibody-generation",
  ];

  return {
    command,
    args,
    cwd: outputDirectory,
    outputDirectory,
    inputPath,
  };
}

function getGeneratedHypothesisCount(options: ProductRunSafeOptions) {
  if (!options.includeGeneratedHypotheses) return 0;

  const requested = options.maxGeneratedHypotheses ?? safeGeneratedHypothesisLimit;
  return Math.min(Math.max(0, Math.floor(requested)), safeGeneratedHypothesisLimit);
}

function waitForSpawnedRun(child: ChildProcessByStdio<null, Readable, Readable>, timeoutMs: number) {
  return new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const maxCapturedBytes = 64_000;
    const timeout = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, timeoutMs);

    child.stdout.on("data", (chunk: Buffer) => {
      stdout = appendBounded(stdout, chunk, maxCapturedBytes);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = appendBounded(stderr, chunk, maxCapturedBytes);
    });
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timeout);
      if (timedOut) {
        reject(new ProductRunExecutionError("Engine run timed out.", `${stdout}\n${stderr}`));
        return;
      }

      resolve({ code, stdout, stderr });
    });
  });
}

function redactedDiagnosticsArtifact(outputDirectory: string, diagnostics: string): EngineArtifact | null {
  if (!diagnostics.trim()) return null;

  const contentText = JSON.stringify(
    {
      diagnostics,
      redacted: true,
      visibility: "admin_only",
    },
    null,
    2,
  );

  return {
    artifactType: "trace_redacted_json",
    path: path.join(outputDirectory, "trace_redacted.json"),
    contentText,
    contentJson: JSON.parse(contentText) as Record<string, unknown>,
    sha256: createHash("sha256").update(contentText).digest("hex"),
    sizeBytes: new TextEncoder().encode(contentText).byteLength,
    publicToUser: false,
    adminOnly: true,
    metadata: { generated_by: "product_engine_runner" },
  };
}

export async function executeEngineRun(
  run: ProductRun,
  options = (run.options ?? {}) as unknown as ProductRunSafeOptions,
  spawnFn: EngineRunSpawn = spawn,
): Promise<EngineRunResult> {
  if (!booleanFromEnv(process.env.PRODUCT_ENABLE_ENGINE_RUNNER)) {
    throw new ProductRunConfigurationError("PRODUCT_ENABLE_ENGINE_RUNNER must be enabled before engine execution.");
  }

  const outputDirectory = await createRunWorkingDirectory(run);
  const commandSpec = buildEngineRunCommand(run, options);
  await writeEngineInputFile(run, options, commandSpec.inputPath);

  const child = spawnFn(commandSpec.command, commandSpec.args, {
    cwd: outputDirectory,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const result = await waitForSpawnedRun(child, getTimeoutMs());

  if (result.code !== 0) {
    throw new ProductRunExecutionError("Engine run failed.", `${result.stdout}\n${result.stderr}`);
  }

  const diagnostics = safeEngineError(new ProductRunExecutionError("", `${result.stdout}\n${result.stderr}`)).diagnostics;
  const diagnosticsArtifact = redactedDiagnosticsArtifact(outputDirectory, diagnostics);
  const artifacts = await collectEngineArtifacts(run);
  if (diagnosticsArtifact) artifacts.push(diagnosticsArtifact);
  const filteredArtifacts = filterArtifactsForProduct(artifacts);

  return {
    status: "succeeded",
    artifacts: filteredArtifacts,
    summary: summarizeEngineResult(filteredArtifacts),
    diagnostics,
  };
}

export async function collectEngineArtifacts(run: ProductRun) {
  return collectEngineArtifactsFromDirectory(getRunOutputDirectory(run), { runSucceeded: true });
}

export async function runProductSafeDiscoveryWorkflow({
  project,
  run,
  options,
}: {
  project: Project;
  run: ProductRun;
  options: ProductRunSafeOptions;
}): Promise<ProductSafeResultBundle> {
  if (isMockEngineRunnerMode(options)) {
    return runMockProductSafeDiscoveryWorkflow({ project, run, options });
  }

  if (booleanFromEnv(process.env.PRODUCT_ENABLE_ENGINE_RUNNER)) {
    try {
      const engineResult = await executeEngineRun(run, options);
      const summary = engineResult.summary;
      const sections =
        summary.sections.length > 0
          ? summary.sections
          : [
              "Run configuration",
              "Candidate ranking summary",
              "Evidence coverage summary",
              ...(summary.generatedHypothesisCount > 0 ? ["Generated hypotheses summary"] : []),
              "Limitations",
              "Guardrail notices",
            ];

      return {
        artifactType: "result_bundle_json",
        displayName: `${project.name} result bundle`,
        summary: {
          status: "Ready for review",
          sections,
          candidateCount: summary.candidateCount,
          evidenceItemCount: summary.evidenceItemCount,
          generatedHypothesisCount: summary.generatedHypothesisCount,
          warningCount: summary.warningCount,
          mode: options.mode,
        },
        payload: {
          product_safe: true,
          project: {
            id: project.id,
            name: project.name,
            disease_focus: project.disease_focus,
            target_focus: project.target_focus,
          },
          run: {
            id: run.id,
            disease_or_goal: run.disease_or_goal,
            mode: run.mode,
          },
          sections,
          guardrails,
          limitations,
          counts: {
            ranked_candidates: summary.candidateCount,
            evidence_items: summary.evidenceItemCount,
            generated_hypotheses: summary.generatedHypothesisCount,
            warnings: summary.warningCount,
          },
        },
      };
    } catch (error) {
      const safeError = safeEngineError(error);
      throw new ProductRunExecutionError(safeError.publicMessage, safeError.diagnostics);
    }
  }

  const generatedHypothesisCount = getGeneratedHypothesisCount(options);
  const sections = [
    "Run configuration",
    "Candidate ranking summary",
    "Evidence coverage summary",
    ...(generatedHypothesisCount > 0 ? ["Generated hypotheses summary"] : []),
    "Limitations",
    "Guardrail notices",
  ];
  const candidateCount = 4;
  const evidenceItemCount = 6;
  const warningCount = 4 + generatedHypothesisCount;

  return {
    artifactType: "result_bundle_json",
    displayName: `${project.name} result bundle`,
    summary: {
      status: "Ready for review",
      sections,
      candidateCount,
      evidenceItemCount,
      generatedHypothesisCount,
      warningCount,
      mode: options.mode,
    },
    payload: {
      product_safe: true,
      project: {
        id: project.id,
        name: project.name,
        disease_focus: project.disease_focus,
        target_focus: project.target_focus,
      },
      run: {
        id: run.id,
        disease_or_goal: run.disease_or_goal,
        mode: run.mode,
      },
      sections,
      guardrails,
      limitations,
      counts: {
        ranked_candidates: candidateCount,
        evidence_items: evidenceItemCount,
        generated_hypotheses: generatedHypothesisCount,
        warnings: warningCount,
      },
    },
  };
}

export function resultSummaryToJson(result: ProductSafeResultBundle): Json {
  return result.summary as unknown as Json;
}

export function resultPayloadToJson(result: ProductSafeResultBundle): Json {
  return result.payload as unknown as Json;
}
