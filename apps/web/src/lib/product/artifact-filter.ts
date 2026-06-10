import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

import { ProductRunConfigurationError } from "./run-errors";
import { redactEngineDiagnostics } from "./run-errors";

export type EngineArtifact = {
  artifactType: string;
  path: string;
  contentText: string;
  contentJson: Record<string, unknown> | null;
  sha256: string;
  sizeBytes: number;
  publicToUser: boolean;
  adminOnly: boolean;
  metadata: Record<string, unknown>;
};

export type ProductArtifactSummary = {
  sections: string[];
  candidateCount: number;
  evidenceItemCount: number;
  generatedHypothesisCount: number;
  warningCount: number;
  validationStatus: string;
};

const productArtifactFiles: Record<
  string,
  {
    artifactType: string;
    publicToUser: boolean;
    adminOnly: boolean;
    redactText?: boolean;
  }
> = {
  "result_bundle.json": { artifactType: "result_bundle_json", publicToUser: true, adminOnly: false },
  "v3_result_bundle.json": { artifactType: "result_bundle_json", publicToUser: true, adminOnly: false },
  "release_result_bundle.json": { artifactType: "result_bundle_json", publicToUser: true, adminOnly: false },
  "result_bundle.md": { artifactType: "result_bundle_markdown", publicToUser: true, adminOnly: false },
  "v3_result_bundle.md": { artifactType: "result_bundle_markdown", publicToUser: true, adminOnly: false },
  "release_result_bundle.md": { artifactType: "result_bundle_markdown", publicToUser: true, adminOnly: false },
  "candidates_summary.json": { artifactType: "candidates_json", publicToUser: true, adminOnly: false },
  "candidate_summary.json": { artifactType: "candidates_json", publicToUser: true, adminOnly: false },
  "v3_candidates_summary.json": { artifactType: "candidates_json", publicToUser: true, adminOnly: false },
  "generated_hypotheses_summary.json": {
    artifactType: "generated_candidates_json",
    publicToUser: true,
    adminOnly: false,
  },
  "v3_generated_hypotheses_summary.json": {
    artifactType: "generated_candidates_json",
    publicToUser: true,
    adminOnly: false,
  },
  "evidence_summary.json": { artifactType: "evidence_json", publicToUser: true, adminOnly: false },
  "v3_evidence_summary.json": { artifactType: "evidence_json", publicToUser: true, adminOnly: false },
  "validation_summary.json": { artifactType: "validation_json", publicToUser: true, adminOnly: false },
  "v3_validation_summary.json": { artifactType: "validation_json", publicToUser: true, adminOnly: false },
  "trace_redacted.json": { artifactType: "trace_redacted_json", publicToUser: false, adminOnly: true, redactText: true },
  "redacted_trace.json": { artifactType: "trace_redacted_json", publicToUser: false, adminOnly: true, redactText: true },
  "engine_diagnostics_redacted.json": {
    artifactType: "engine_diagnostics_redacted_json",
    publicToUser: false,
    adminOnly: true,
    redactText: true,
  },
  "redacted_engine_diagnostics.json": {
    artifactType: "engine_diagnostics_redacted_json",
    publicToUser: false,
    adminOnly: true,
    redactText: true,
  },
  "runtime_summary_redacted.json": {
    artifactType: "runtime_summary_redacted_json",
    publicToUser: false,
    adminOnly: true,
    redactText: true,
  },
  "redacted_runtime_summary.json": {
    artifactType: "runtime_summary_redacted_json",
    publicToUser: false,
    adminOnly: true,
    redactText: true,
  },
};

type ArtifactFileConfig = (typeof productArtifactFiles)[string];

const blockedArtifactPatterns = [
  /(^|[/\\])\.env(\.|$)/i,
  /(^|[/\\])\.cache([/\\]|$)/i,
  /(^|[/\\])cache([/\\]|$)/i,
  /codex.*transcript/i,
  /transcript/i,
  /stdout/i,
  /stderr/i,
  /(^|[/\\])logs?([/\\]|$)/i,
  /\.log$/i,
  /secret/i,
  /credential/i,
  /external[_-]?payload/i,
  /raw[_-]?payload/i,
  /raw[_-]?trace/i,
  /internal[_-]?trace/i,
  /policy/i,
  /governance/i,
  /repair/i,
];

const ignoredEngineFiles = new Set(["product_run_input.json"]);

function maxArtifactBytes() {
  const parsed = Number.parseInt(process.env.PRODUCT_MAX_ARTIFACT_BYTES ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1_000_000;
}

function parseJsonObject(content: string) {
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function artifactPayloadForHash(contentText: string, contentJson: Record<string, unknown> | null) {
  return contentJson ? JSON.stringify(contentJson) : contentText;
}

function numberField(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function stringArrayField(value: unknown) {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : [];
}

function relativeArtifactPath(outputDirectory: string, filePath: string) {
  const relativePath = path.relative(outputDirectory, filePath);

  if (!relativePath || relativePath.startsWith("..") || path.isAbsolute(relativePath)) {
    throw new ProductRunConfigurationError("Engine artifact path escapes the run output directory.");
  }

  return relativePath;
}

function rejectUnsafeArtifactPath(outputDirectory: string, filePath: string) {
  const relativePath = relativeArtifactPath(outputDirectory, filePath);
  const normalized = relativePath.toLowerCase();

  if (blockedArtifactPatterns.some((pattern) => pattern.test(normalized))) {
    throw new ProductRunConfigurationError("Engine artifact path is blocked from product artifact storage.");
  }
}

function classifyArtifact(outputDirectory: string, artifactPath: string): { fileName: string; relativePath: string; config: ArtifactFileConfig } {
  rejectUnsafeArtifactPath(outputDirectory, artifactPath);

  const relativePath = relativeArtifactPath(outputDirectory, artifactPath);
  const fileName = path.basename(relativePath);
  const config = productArtifactFiles[fileName];

  if (!config) {
    throw new ProductRunConfigurationError(`Unknown engine artifact is not product-safe: ${relativePath}`);
  }

  return { fileName, relativePath, config };
}

async function listArtifactFiles(outputDirectory: string) {
  const entries = await fs.readdir(outputDirectory, { withFileTypes: true });
  const files: string[] = [];

  for (const entry of entries) {
    const entryPath = path.join(outputDirectory, entry.name);
    if (entry.isDirectory()) {
      rejectUnsafeArtifactPath(outputDirectory, entryPath);
      const childFiles = await listArtifactFiles(entryPath);
      files.push(...childFiles);
      continue;
    }

    if (entry.isFile()) files.push(entryPath);
  }

  return files.sort((a, b) => a.localeCompare(b));
}

function fallbackResultBundleArtifact(outputDirectory: string, artifacts: EngineArtifact[]): EngineArtifact {
  const summary = summarizeEngineResult(artifacts);
  const sections =
    summary.sections.length > 0
      ? summary.sections
      : [
          "Candidate ranking summary",
          "Evidence coverage summary",
          ...(summary.generatedHypothesisCount > 0 ? ["Generated hypotheses summary"] : []),
          "Validation summary",
          "Limitations",
        ];
  const contentJson = {
    product_safe: true,
    fallback: true,
    status: "Ready for review",
    sections,
    candidateCount: summary.candidateCount,
    evidenceItemCount: summary.evidenceItemCount,
    generatedHypothesisCount: summary.generatedHypothesisCount,
    warningCount: summary.warningCount,
    validationStatus: summary.validationStatus,
    limitations: [
      "Full result bundle artifact was not produced by the bounded engine run.",
      "This fallback summary was assembled from product-safe summary artifacts only.",
      "Human review is required before any downstream research planning use.",
    ],
  };
  const contentText = JSON.stringify(contentJson, null, 2);
  const shaPayload = artifactPayloadForHash(contentText, contentJson);

  return {
    artifactType: "result_bundle_json",
    path: path.join(outputDirectory, "fallback_result_bundle.json"),
    contentText,
    contentJson,
    sha256: createHash("sha256").update(shaPayload).digest("hex"),
    sizeBytes: new TextEncoder().encode(contentText).byteLength,
    publicToUser: true,
    adminOnly: false,
    metadata: { file_name: "fallback_result_bundle.json", generated_by: "product_artifact_filter", fallback: true },
  };
}

export async function collectEngineArtifactsFromDirectory(outputDirectory: string, options: { runSucceeded?: boolean } = {}) {
  const artifacts: EngineArtifact[] = [];
  const maxBytes = maxArtifactBytes();

  for (const artifactPath of await listArtifactFiles(outputDirectory)) {
    if (ignoredEngineFiles.has(path.basename(artifactPath))) continue;

    const { fileName, relativePath, config } = classifyArtifact(outputDirectory, artifactPath);
    const stats = await fs.stat(artifactPath);
    if (stats.size > maxBytes) {
      throw new ProductRunConfigurationError("Engine artifact exceeds the configured product artifact size limit.");
    }

    const rawContentText = await fs.readFile(artifactPath, "utf8");
    const contentText = config.redactText ? redactEngineDiagnostics(rawContentText) : rawContentText;
    const contentJson = fileName.endsWith(".json") ? parseJsonObject(contentText) : null;
    const shaPayload = artifactPayloadForHash(contentText, contentJson);
    artifacts.push({
      artifactType: config.artifactType,
      path: artifactPath,
      contentText,
      contentJson,
      sha256: createHash("sha256").update(shaPayload).digest("hex"),
      sizeBytes: new TextEncoder().encode(contentText).byteLength,
      publicToUser: config.publicToUser,
      adminOnly: config.adminOnly,
      metadata: { file_name: fileName, relative_path: relativePath },
    });
  }

  if (options.runSucceeded && !artifacts.some((artifact) => artifact.artifactType === "result_bundle_json")) {
    artifacts.unshift(fallbackResultBundleArtifact(outputDirectory, artifacts));
  }

  return artifacts;
}

export function filterArtifactsForProduct(runArtifacts: EngineArtifact[]) {
  return runArtifacts.filter((artifact) => artifact.artifactType in artifactTypeAllowlist);
}

const artifactTypeAllowlist = {
  result_bundle_json: true,
  result_bundle_markdown: true,
  candidates_json: true,
  generated_candidates_json: true,
  evidence_json: true,
  validation_json: true,
  trace_redacted_json: true,
  engine_diagnostics_redacted_json: true,
  runtime_summary_redacted_json: true,
} as const;

export function summarizeEngineResult(runArtifacts: EngineArtifact[]): ProductArtifactSummary {
  const resultBundle = runArtifacts.find((artifact) => artifact.artifactType === "result_bundle_json")?.contentJson ?? {};
  const candidates = runArtifacts.find((artifact) => artifact.artifactType === "candidates_json")?.contentJson ?? {};
  const evidence = runArtifacts.find((artifact) => artifact.artifactType === "evidence_json")?.contentJson ?? {};
  const generated = runArtifacts.find((artifact) => artifact.artifactType === "generated_candidates_json")?.contentJson ?? {};
  const validation = runArtifacts.find((artifact) => artifact.artifactType === "validation_json")?.contentJson ?? {};

  return {
    sections: stringArrayField(resultBundle.sections),
    candidateCount: numberField(resultBundle.candidateCount) || numberField(candidates.count),
    evidenceItemCount: numberField(resultBundle.evidenceItemCount) || numberField(evidence.count),
    generatedHypothesisCount: numberField(resultBundle.generatedHypothesisCount) || numberField(generated.count),
    warningCount: numberField(resultBundle.warningCount) || numberField(validation.warningCount),
    validationStatus: typeof validation.status === "string" ? validation.status : "not_available",
  };
}
