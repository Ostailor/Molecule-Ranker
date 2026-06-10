import { productApiError } from "./api-errors";
import type { Json, ProductRunMode } from "@/lib/supabase/types";

export type ProductRunSafeOptions = {
  mode: ProductRunMode;
  includeGeneratedHypotheses: boolean;
  maxGeneratedHypotheses: number;
  prepareResultBundle: boolean;
  externalWritesEnabled: false;
  writeApprovedLiveEnabled: false;
  antibodyGenerationEnabled: false;
  externalIntegrationsEnabled: false;
  exposeRawEngineInternals: false;
  exposeRawCodexTranscript: false;
  exposeRawTraceLogs: false;
};

export const defaultProductRunSafeOptions: ProductRunSafeOptions = {
  mode: "dry_run",
  includeGeneratedHypotheses: false,
  maxGeneratedHypotheses: 3,
  prepareResultBundle: true,
  externalWritesEnabled: false,
  writeApprovedLiveEnabled: false,
  antibodyGenerationEnabled: false,
  externalIntegrationsEnabled: false,
  exposeRawEngineInternals: false,
  exposeRawCodexTranscript: false,
  exposeRawTraceLogs: false,
};

type RunInput = {
  mode?: unknown;
  include_generated_hypotheses?: unknown;
  max_generated_hypotheses?: unknown;
  prepare_result_bundle?: unknown;
  acknowledgement?: unknown;
  disease_or_goal?: unknown;
  disease_project_objective?: unknown;
  target_focus?: unknown;
  external_writes?: unknown;
  externalWrites?: unknown;
  write_approved_live?: unknown;
  writeApprovedLive?: unknown;
  enable_antibody_generation?: unknown;
  enableAntibodyGeneration?: unknown;
  antibody_generation?: unknown;
};

const unsafeFreeTextMessage = "Do not request treatment, dosing, synthesis, or lab protocols.";
const unsafeFreeTextPatterns = [
  /\bdosing\b/i,
  /\bdose\b/i,
  /\bprotocol\b/i,
  /\bsynthesis\b/i,
  /\bsynthesize\b/i,
  /\badminister\b/i,
  /\bpatient\s+treatment\b/i,
];

export function textField(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function booleanField(value: unknown, fallback: boolean) {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;

  return fallback;
}

function assertUnsafeFlagsDisabled(input: RunInput) {
  if (
    booleanField(input.external_writes, false) ||
    booleanField(input.externalWrites, false) ||
    booleanField(input.write_approved_live, false) ||
    booleanField(input.writeApprovedLive, false)
  ) {
    throw productApiError("VALIDATION_ERROR", "External writes and write-approved mode are disabled for V0.3 discovery runs.");
  }

  if (
    booleanField(input.enable_antibody_generation, false) ||
    booleanField(input.enableAntibodyGeneration, false) ||
    booleanField(input.antibody_generation, false)
  ) {
    throw productApiError("VALIDATION_ERROR", "Antibody generation is disabled for V0.3 discovery runs.");
  }
}

function assertSafeFreeText(input: RunInput) {
  const fields = [input.disease_or_goal, input.disease_project_objective, input.target_focus];

  for (const field of fields) {
    if (typeof field !== "string") continue;
    if (unsafeFreeTextPatterns.some((pattern) => pattern.test(field))) {
      throw productApiError("VALIDATION_ERROR", unsafeFreeTextMessage);
    }
  }
}

export function normalizeRunMode(value: unknown): ProductRunMode {
  if (value === "dry_run" || value === "mocked") return value;
  if (value === "read_only_live" && booleanField(process.env.PRODUCT_READ_ONLY_LIVE_RUNS_ENABLED, false)) return value;
  if (value === "read_only_live") {
    throw productApiError("VALIDATION_ERROR", "read_only_live mode is disabled for V0.3.");
  }

  throw productApiError("VALIDATION_ERROR", "Choose mocked, dry_run, or an enabled read_only_live workflow mode.");
}

export function readSafeRunOptions(input: RunInput): ProductRunSafeOptions {
  if (input.acknowledgement !== true) {
    throw productApiError("VALIDATION_ERROR", "Acknowledge the research-use boundary before starting a run.");
  }

  assertUnsafeFlagsDisabled(input);
  assertSafeFreeText(input);

  return {
    ...defaultProductRunSafeOptions,
    mode: normalizeRunMode(input.mode ?? defaultProductRunSafeOptions.mode),
    includeGeneratedHypotheses: booleanField(input.include_generated_hypotheses, false),
    maxGeneratedHypotheses: readMaxGeneratedHypotheses(input.max_generated_hypotheses),
    prepareResultBundle: booleanField(input.prepare_result_bundle, true),
  };
}

function readMaxGeneratedHypotheses(value: unknown) {
  if (value === undefined || value === null || value === "") return defaultProductRunSafeOptions.maxGeneratedHypotheses;

  const parsed = typeof value === "number" ? value : Number.parseInt(String(value), 10);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > defaultProductRunSafeOptions.maxGeneratedHypotheses) {
    throw productApiError(
      "VALIDATION_ERROR",
      `Generated hypothesis limit must be between 0 and ${defaultProductRunSafeOptions.maxGeneratedHypotheses}.`,
    );
  }

  return parsed;
}

export function safeOptionsToJson(options: ProductRunSafeOptions & Record<string, unknown>): Json {
  return options as unknown as Json;
}

export async function readJsonObject(request: Request): Promise<Record<string, unknown>> {
  try {
    const body = await request.json();
    if (typeof body === "object" && body !== null && !Array.isArray(body)) return body as Record<string, unknown>;
  } catch {
    throw productApiError("VALIDATION_ERROR", "Request body must be valid JSON.");
  }

  throw productApiError("VALIDATION_ERROR", "Request body must be a JSON object.");
}
