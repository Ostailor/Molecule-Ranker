import { createHash } from "node:crypto";
import path from "node:path";

import { productApiError } from "./api-errors";
import type { Json, ProductRole, ProductRunArtifact } from "@/lib/supabase/types";

export type ProductArtifactStorageKind = "database" | "local_file" | "supabase_storage";

export type ProductArtifactAccessContext = {
  organization: {
    id: string;
  };
  role: ProductRole;
  projectId?: string;
  runId?: string;
};

export type ProductArtifactInput = {
  artifactType: string;
  storageKind?: ProductArtifactStorageKind;
  storagePath?: string | null;
  contentJson?: Json | null;
  contentText?: string | null;
  sha256?: string | null;
  sizeBytes?: number | null;
  publicToUser?: boolean;
  adminOnly?: boolean;
  metadata?: Json;
};

const productArtifactTypeAllowlist = new Set([
  "result_bundle_json",
  "result_bundle_markdown",
  "candidates_json",
  "generated_candidates_json",
  "evidence_json",
  "trace_redacted_json",
  "engine_diagnostics_redacted_json",
  "runtime_summary_redacted_json",
  "validation_json",
]);

function isAdminRole(role: ProductRole) {
  return role === "owner" || role === "admin";
}

function stableArtifactPayload(value: unknown): string {
  if (typeof value === "string") return value;
  if (value instanceof Uint8Array) return Buffer.from(value).toString("utf8");

  return JSON.stringify(value ?? null);
}

function maxArtifactBytes() {
  const parsed = Number.parseInt(process.env.PRODUCT_MAX_ARTIFACT_BYTES ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1_000_000;
}

function normalizedPathSegments(storagePath: string) {
  if (path.isAbsolute(storagePath)) {
    throw productApiError("VALIDATION_ERROR", "Artifact paths must be relative to the run working directory.");
  }

  const normalized = path.normalize(storagePath);
  const segments = normalized.split(/[\\/]+/).filter(Boolean);

  if (normalized.startsWith("..") || segments.includes("..")) {
    throw productApiError("VALIDATION_ERROR", "Artifact paths cannot traverse outside the run working directory.");
  }

  return segments;
}

function validateArtifactPath(storagePath: string | null | undefined) {
  if (!storagePath) return;

  const segments = normalizedPathSegments(storagePath);
  const lowerSegments = segments.map((segment) => segment.toLowerCase());

  if (lowerSegments.includes("cache") || lowerSegments.includes(".cache")) {
    throw productApiError("VALIDATION_ERROR", "Cache files cannot be stored as product artifacts.");
  }

  if (lowerSegments.some((segment) => segment === ".env" || segment.startsWith(".env."))) {
    throw productApiError("VALIDATION_ERROR", "Environment files cannot be stored as product artifacts.");
  }
}

function validateArtifactType(artifactType: string, publicToUser: boolean) {
  if (!artifactType.trim()) {
    throw productApiError("VALIDATION_ERROR", "Artifacts must have a type.");
  }

  if (!productArtifactTypeAllowlist.has(artifactType)) {
    throw productApiError("VALIDATION_ERROR", "Artifact type is not product-safe.");
  }

  if (
    publicToUser &&
    (artifactType === "trace_redacted_json" ||
      artifactType === "engine_diagnostics_redacted_json" ||
      artifactType === "runtime_summary_redacted_json")
  ) {
    throw productApiError("VALIDATION_ERROR", "Trace diagnostics cannot be exposed to normal users.");
  }

  if (/raw|transcript|tool_log|repair_log|governance|secret|credential|cache|env/i.test(artifactType)) {
    throw productApiError("VALIDATION_ERROR", "Artifact type is not product-safe.");
  }
}

export function computeArtifactSha256(value: unknown) {
  return createHash("sha256").update(stableArtifactPayload(value)).digest("hex");
}

export function estimateArtifactSize(value: unknown) {
  return new TextEncoder().encode(stableArtifactPayload(value)).byteLength;
}

export function validateArtifactForProductExposure(artifact: ProductArtifactInput) {
  const storageKind = artifact.storageKind ?? "database";
  const publicToUser = artifact.publicToUser ?? true;
  const sizeBytes =
    artifact.sizeBytes ??
    estimateArtifactSize(artifact.contentText ?? artifact.contentJson ?? artifact.storagePath ?? artifact.artifactType);

  validateArtifactType(artifact.artifactType, publicToUser);
  validateArtifactPath(artifact.storagePath);

  if (storageKind === "local_file" && process.env.NODE_ENV === "production") {
    throw productApiError("VALIDATION_ERROR", "Local file artifact storage is available only for development.");
  }

  if (storageKind !== "database" && storageKind !== "local_file" && storageKind !== "supabase_storage") {
    throw productApiError("VALIDATION_ERROR", "Unsupported artifact storage kind.");
  }

  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) {
    throw productApiError("VALIDATION_ERROR", "Artifacts must have a positive size.");
  }

  if (sizeBytes > maxArtifactBytes()) {
    throw productApiError("VALIDATION_ERROR", "Artifact exceeds the configured product artifact size limit.");
  }

  return {
    ...artifact,
    storageKind,
    sizeBytes,
    sha256: artifact.sha256 ?? computeArtifactSha256(artifact.contentText ?? artifact.contentJson ?? artifact.storagePath ?? artifact.artifactType),
    publicToUser,
    adminOnly: artifact.adminOnly ?? false,
    metadata: artifact.metadata ?? {},
  };
}

export function artifactVisibilityFilter(context: ProductArtifactAccessContext, artifact: ProductRunArtifact) {
  if (artifact.organization_id !== context.organization.id) return false;
  if (context.projectId && artifact.project_id !== context.projectId) return false;
  if (context.runId && artifact.run_id !== context.runId) return false;

  if (artifact.admin_only) return isAdminRole(context.role);
  if (!artifact.public_to_user) return isAdminRole(context.role);

  return true;
}
