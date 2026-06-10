import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

type RunStatusRouteContext = {
  params: Promise<{
    projectId: string;
    runId: string;
  }>;
};

const statusSelect = "id, organization_id, project_id, status, progress, error_summary, result_summary, started_at, completed_at, updated_at";

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export async function GET(_request: Request, { params }: RunStatusRouteContext) {
  try {
    const { projectId, runId } = await params;

    if (!isUuid(projectId) || !isUuid(runId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:read", supabase);
    const { data, error } = await supabase
      .from("product_runs")
      .select(statusSelect)
      .eq("id", runId)
      .eq("project_id", projectId)
      .eq("organization_id", context.organization.id)
      .maybeSingle();

    if (error || !data) {
      throw productApiError("NOT_FOUND");
    }

    return success({
      status: data.status,
      progress: data.progress,
      error_summary: data.error_summary,
      result_summary: data.result_summary,
      started_at: data.started_at,
      completed_at: data.completed_at,
      updated_at: data.updated_at,
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
