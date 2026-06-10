import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { listRunArtifacts } from "@/lib/product/artifact-storage";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

type ResultBundleRouteContext = {
  params: Promise<{
    projectId: string;
    runId: string;
  }>;
};

const runSelect = "id, organization_id, project_id, status, progress, result_summary, error_summary, completed_at, created_at";

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export async function GET(_request: Request, { params }: ResultBundleRouteContext) {
  try {
    const { projectId, runId } = await params;

    if (!isUuid(projectId) || !isUuid(runId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:read", supabase);
    const { data: runData, error: runError } = await supabase
      .from("product_runs")
      .select(runSelect)
      .eq("id", runId)
      .eq("project_id", projectId)
      .eq("organization_id", context.organization.id)
      .maybeSingle();

    if (runError || !runData) {
      throw productApiError("NOT_FOUND");
    }

    const artifacts = await listRunArtifacts({ ...context, supabase, projectId, runId }, runId);
    const artifact = artifacts.find((item) => item.artifact_type === "result_bundle_json") ?? null;

    return success({
      run: {
        id: runData.id,
        status: runData.status,
        progress: runData.progress,
        error_summary: runData.error_summary,
        result_summary: runData.result_summary,
      },
      artifact,
      artifacts,
      summary: runData.result_summary,
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
