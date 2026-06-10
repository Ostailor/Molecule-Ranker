import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { getRunArtifact } from "@/lib/product/artifact-storage";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

type ArtifactDetailRouteContext = {
  params: Promise<{
    projectId: string;
    runId: string;
    artifactId: string;
  }>;
};

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export async function GET(_request: Request, { params }: ArtifactDetailRouteContext) {
  try {
    const { projectId, runId, artifactId } = await params;

    if (!isUuid(projectId) || !isUuid(runId) || !isUuid(artifactId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:read", supabase);
    const artifact = await getRunArtifact({ ...context, supabase, projectId, runId }, artifactId);

    return success({ artifact });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
