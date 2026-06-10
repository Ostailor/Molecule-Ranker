import { getProductFeatureFlags } from "./feature-flags";
import type { ProductFeatureFlags } from "./feature-flags";
import type { ProductRunMode, ProductRunOptions, ProductRunStatus } from "./types";

const productRunModes = ["mocked", "dry_run", "read_only_live"] as const;
const terminalRunStatuses = ["succeeded", "failed", "partially_succeeded", "cancelled"] as const;
const successfulRunStatuses = ["succeeded", "partially_succeeded"] as const;

export const safeMaxGeneratedHypotheses = 3;

type RunOptionInput = Partial<Omit<ProductRunOptions, "externalWrites">> & {
  externalWrites?: unknown;
  externalIntegrations?: unknown;
  writeApprovedLive?: unknown;
};

function booleanFromEnv(value: string | undefined) {
  return value === "1" || value === "true" || value === "TRUE";
}

function isRunMode(value: unknown): value is ProductRunMode {
  return typeof value === "string" && productRunModes.includes(value as ProductRunMode);
}

function isCodexRuntimeEnabled() {
  return booleanFromEnv(process.env.PRODUCT_CODEX_RUNTIME_ENABLED) || booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_CODEX_RUNTIME_ENABLED);
}

function isReadOnlyLiveEnabled() {
  return booleanFromEnv(process.env.PRODUCT_READ_ONLY_LIVE_RUNS_ENABLED);
}

function defaultRunMode(): ProductRunMode {
  return process.env.PRODUCT_DISCOVERY_RUN_MODE === "mocked" || process.env.NEXT_PUBLIC_PRODUCT_DISCOVERY_RUN_MODE === "mocked"
    ? "mocked"
    : "dry_run";
}

export function isTerminalRunStatus(status: ProductRunStatus | string | null | undefined): status is ProductRunStatus {
  return terminalRunStatuses.includes(status as (typeof terminalRunStatuses)[number]);
}

export function isSuccessfulRunStatus(status: ProductRunStatus | string | null | undefined): status is ProductRunStatus {
  return successfulRunStatuses.includes(status as (typeof successfulRunStatuses)[number]);
}

export function runStatusLabel(status: ProductRunStatus | string | null | undefined) {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "succeeded":
      return "Succeeded";
    case "failed":
      return "Failed";
    case "partially_succeeded":
      return "Partially succeeded";
    case "cancelled":
      return "Cancelled";
    default:
      return "Unknown";
  }
}

export function runModeLabel(mode: ProductRunMode | string | null | undefined) {
  switch (mode) {
    case "mocked":
      return "Mocked";
    case "dry_run":
      return "Dry run";
    case "read_only_live":
      return "Read-only live";
    default:
      return "Unknown";
  }
}

export function safeRunOptions(input: RunOptionInput = {}, flags: ProductFeatureFlags = getProductFeatureFlags()): ProductRunOptions {
  validateRunOptions(input, flags);

  const requestedGeneration = input.enableGeneration === true;
  const maxGeneratedHypotheses =
    typeof input.maxGeneratedHypotheses === "number" && Number.isFinite(input.maxGeneratedHypotheses)
      ? Math.max(0, Math.floor(input.maxGeneratedHypotheses))
      : safeMaxGeneratedHypotheses;

  return {
    enableGeneration: flags.generatedHypothesesViewer && requestedGeneration,
    maxGeneratedHypotheses: Math.min(maxGeneratedHypotheses, safeMaxGeneratedHypotheses),
    enableBiologics: false,
    enableAntibodyGeneration: false,
    enableStructure: false,
    enableCodexSummary: isCodexRuntimeEnabled() && input.enableCodexSummary !== false,
    externalWrites: false,
    mode: isRunMode(input.mode) ? input.mode : defaultRunMode(),
  };
}

export function validateRunOptions(input: RunOptionInput = {}, flags: ProductFeatureFlags = getProductFeatureFlags()) {
  if (input.mode !== undefined && !isRunMode(input.mode)) {
    throw new Error("Unsupported discovery run mode.");
  }

  if (input.mode === "read_only_live" && !isReadOnlyLiveEnabled()) {
    throw new Error("read_only_live mode is disabled for V0.3.");
  }

  if (input.externalWrites === true || input.externalIntegrations === true || input.writeApprovedLive === true) {
    throw new Error("External writes and integrations are unavailable for discovery runs.");
  }

  if (input.enableAntibodyGeneration === true) {
    throw new Error("Antibody generation is disabled for discovery runs.");
  }

  if (input.enableStructure === true) {
    throw new Error("Structure workflows are disabled for V0.3 discovery runs.");
  }

  if (input.enableBiologics === true && !flags.biologicsViewer) {
    throw new Error("Biologics workflows are disabled for V0.3 discovery runs.");
  }

  if (input.enableGeneration === true && !flags.generatedHypothesesViewer) {
    throw new Error("Generated hypotheses are disabled by product configuration.");
  }

  if (
    input.maxGeneratedHypotheses !== undefined &&
    (!Number.isInteger(input.maxGeneratedHypotheses) ||
      input.maxGeneratedHypotheses < 0 ||
      input.maxGeneratedHypotheses > safeMaxGeneratedHypotheses)
  ) {
    throw new Error(`Generated hypothesis limit must be between 0 and ${safeMaxGeneratedHypotheses}.`);
  }
}
