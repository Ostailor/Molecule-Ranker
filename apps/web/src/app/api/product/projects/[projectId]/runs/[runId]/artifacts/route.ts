import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { listRunArtifacts } from "@/lib/product/artifact-storage";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

type ArtifactsRouteContext = {
  params: Promise<{
    projectId: string;
    runId: string;
  }>;
};

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export async function GET(_request: Request, { params }: ArtifactsRouteContext) {
  try {
    const { projectId, runId } = await params;

    if (!isUuid(projectId) || !isUuid(runId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:read", supabase);
    const artifacts = await listRunArtifacts({ ...context, supabase, projectId, runId }, runId);

    return success({ artifacts });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
