import { productFeatureFlags } from "@/lib/product/feature-flags";

export const featureFlags = {
  ...productFeatureFlags,
  connectedServices: false,
  realAuth: true,
  generationPreview: productFeatureFlags.generatedHypothesesViewer,
  evidenceTrace: true,
  adminConsole: productFeatureFlags.adminDashboard,
} as const;
