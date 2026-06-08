import { canAccessAdmin } from "./permissions";
import type { ProductRole } from "./types";

export type ProductFeatureFlags = {
  discoveryRunsPlaceholder: boolean;
  generatedHypothesesViewer: boolean;
  biologicsViewer: boolean;
  antibodyGeneration: false;
  externalIntegrations: boolean;
  externalWrites: false;
  adminDashboard: boolean;
  stripeBilling: false;
  stripeBillingPlaceholder: boolean;
  exportsPlaceholder: boolean;
};

export type ClientSafeProductFeatureFlags = Pick<
  ProductFeatureFlags,
  | "discoveryRunsPlaceholder"
  | "generatedHypothesesViewer"
  | "biologicsViewer"
  | "adminDashboard"
  | "stripeBillingPlaceholder"
  | "exportsPlaceholder"
>;

export const releaseDefaultProductFeatureFlags: ProductFeatureFlags = {
  discoveryRunsPlaceholder: true,
  generatedHypothesesViewer: true,
  biologicsViewer: false,
  antibodyGeneration: false,
  externalIntegrations: false,
  externalWrites: false,
  adminDashboard: true,
  stripeBilling: false,
  stripeBillingPlaceholder: true,
  exportsPlaceholder: true,
};

const clientSafeFlagKeys = [
  "discoveryRunsPlaceholder",
  "generatedHypothesesViewer",
  "biologicsViewer",
  "adminDashboard",
  "stripeBillingPlaceholder",
  "exportsPlaceholder",
] as const;

const riskyAlwaysDisabledFlags = ["antibodyGeneration", "externalWrites", "stripeBilling"] as const;

function booleanFromEnv(value: string | undefined) {
  if (value === "1" || value === "true" || value === "TRUE") return true;
  if (value === "0" || value === "false" || value === "FALSE") return false;

  return undefined;
}

function applyClientSafeEnvironmentOverrides(flags: ProductFeatureFlags): ProductFeatureFlags {
  return {
    ...flags,
    discoveryRunsPlaceholder:
      booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_FEATURE_DISCOVERY_RUNS_PLACEHOLDER) ??
      flags.discoveryRunsPlaceholder,
    generatedHypothesesViewer:
      booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_FEATURE_GENERATED_HYPOTHESES_VIEWER) ??
      flags.generatedHypothesesViewer,
    biologicsViewer:
      booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_FEATURE_BIOLOGICS_VIEWER) ?? flags.biologicsViewer,
    adminDashboard:
      booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_FEATURE_ADMIN_DASHBOARD) ?? flags.adminDashboard,
    stripeBillingPlaceholder:
      booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_FEATURE_STRIPE_BILLING_PLACEHOLDER) ??
      flags.stripeBillingPlaceholder,
    exportsPlaceholder:
      booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_FEATURE_EXPORTS_PLACEHOLDER) ?? flags.exportsPlaceholder,
  };
}

function forceRiskyFlagsDisabled(flags: ProductFeatureFlags): ProductFeatureFlags {
  return {
    ...flags,
    antibodyGeneration: false,
    externalWrites: false,
    stripeBilling: false,
  };
}

export function getProductFeatureFlags() {
  return forceRiskyFlagsDisabled(applyClientSafeEnvironmentOverrides(releaseDefaultProductFeatureFlags));
}

export function getClientSafeProductFeatureFlags(): ClientSafeProductFeatureFlags {
  const flags = getProductFeatureFlags();

  return Object.fromEntries(clientSafeFlagKeys.map((key) => [key, flags[key]])) as ClientSafeProductFeatureFlags;
}

export function canShowAdminDashboard(role: ProductRole | null | undefined, flags = getProductFeatureFlags()) {
  return flags.adminDashboard && canAccessAdmin(role ?? "viewer");
}

export function canShowFeatureFlagToClient(flag: keyof ProductFeatureFlags) {
  return clientSafeFlagKeys.includes(flag as (typeof clientSafeFlagKeys)[number]);
}

export const productFeatureFlags = getClientSafeProductFeatureFlags();

export const productServerFeatureFlagDefaults = forceRiskyFlagsDisabled(releaseDefaultProductFeatureFlags);

export const unsafeFeatureFlagsAlwaysDisabled = riskyAlwaysDisabledFlags;
