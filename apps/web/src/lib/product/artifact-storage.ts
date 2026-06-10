import { productApiError } from "./api-errors";
import {
  artifactVisibilityFilter,
  validateArtifactForProductExposure,
  type ProductArtifactInput,
  type ProductArtifactAccessContext,
} from "./artifacts";
import { resultPayloadToJson, type ProductSafeResultBundle } from "./engine-runner";
import type { ProductAuthSupabaseClient, ProductRouteAuthContext } from "./auth-context";
import type { Json, ProductRun, ProductRunArtifact } from "@/lib/supabase/types";

const artifactSelect =
  "id, organization_id, project_id, run_id, artifact_type, storage_kind, storage_path, content_json, content_text, sha256, size_bytes, public_to_user, admin_only, created_at, metadata";

type RunArtifactStorageContext = {
  context: ProductRouteAuthContext;
  supabase: ProductAuthSupabaseClient;
  run: Pick<ProductRun, "id" | "organization_id" | "project_id">;
};

type ArtifactReadContext = ProductArtifactAccessContext & {
  supabase: ProductAuthSupabaseClient;
};

function toAccessContext(context: ProductRouteAuthContext, projectId?: string, runId?: string): ProductArtifactAccessContext {
  return {
    organization: context.organization,
    role: context.role,
    projectId,
    runId,
  };
}

export async function storeRunArtifact(runContext: RunArtifactStorageContext, artifact: ProductArtifactInput) {
  if (runContext.run.organization_id !== runContext.context.organization.id) {
    throw productApiError("FORBIDDEN", "Artifact run is outside the active organization.");
  }

  const safeArtifact = validateArtifactForProductExposure(artifact);
  const { data, error } = await runContext.supabase
    .from("product_run_artifacts")
    .insert({
      organization_id: runContext.context.organization.id,
      project_id: runContext.run.project_id,
      run_id: runContext.run.id,
      artifact_type: safeArtifact.artifactType,
      storage_kind: safeArtifact.storageKind,
      storage_path: safeArtifact.storagePath ?? null,
      content_json: safeArtifact.contentJson ?? null,
      content_text: safeArtifact.contentText ?? null,
      sha256: safeArtifact.sha256,
      size_bytes: safeArtifact.sizeBytes,
      public_to_user: safeArtifact.publicToUser,
      admin_only: safeArtifact.adminOnly,
      metadata: safeArtifact.metadata,
    })
    .select(artifactSelect)
    .single();

  if (error || !data) {
    throw productApiError("FORBIDDEN", "Could not store the product-safe run artifact.");
  }

  return data as ProductRunArtifact;
}

export async function getRunArtifact(context: ArtifactReadContext, artifactId: string) {
  const { data, error } = await context.supabase
    .from("product_run_artifacts")
    .select(artifactSelect)
    .eq("id", artifactId)
    .eq("organization_id", context.organization.id)
    .maybeSingle();

  if (error || !data) {
    throw productApiError("NOT_FOUND");
  }

  const artifact = data as ProductRunArtifact;
  if (!artifactVisibilityFilter(context, artifact)) {
    throw productApiError("NOT_FOUND");
  }

  return artifact;
}

export async function listRunArtifacts(context: ArtifactReadContext, runId: string) {
  const { data, error } = await context.supabase
    .from("product_run_artifacts")
    .select(artifactSelect)
    .eq("organization_id", context.organization.id)
    .eq("run_id", runId)
    .order("created_at", { ascending: false });

  if (error) {
    throw productApiError("FORBIDDEN");
  }

  return ((data ?? []) as ProductRunArtifact[]).filter((artifact) => artifactVisibilityFilter({ ...context, runId }, artifact));
}

export async function storeProductSafeResultArtifact({
  context,
  projectId,
  run,
  result,
  supabase,
}: {
  context: ProductRouteAuthContext;
  projectId: string;
  run: ProductRun;
  result: ProductSafeResultBundle;
  supabase: ProductAuthSupabaseClient;
}) {
  if (run.project_id !== projectId) {
    throw productApiError("FORBIDDEN", "Artifact project does not match the run.");
  }

  const contentJson = {
    summary: result.summary,
    payload: resultPayloadToJson(result),
  } as Json;

  return storeRunArtifact(
    { context, supabase, run },
    {
      artifactType: result.artifactType,
      storageKind: "database",
      contentJson,
      publicToUser: true,
      adminOnly: false,
      metadata: {
        display_name: result.displayName,
        created_by_user_id: context.user.id,
      },
    },
  );
}

export { artifactVisibilityFilter, toAccessContext };
