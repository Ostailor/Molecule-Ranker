import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

type RunRouteContext = {
  params: Promise<{
    projectId: string;
    runId: string;
  }>;
};

const runSelect =
  "id, organization_id, project_id, created_by_user_id, run_type, mode, status, disease_or_goal, target_focus, options, progress, result_summary, error_summary, started_at, completed_at, created_at, updated_at";

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export async function GET(_request: Request, { params }: RunRouteContext) {
  try {
    const { projectId, runId } = await params;

    if (!isUuid(projectId) || !isUuid(runId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:read", supabase);
    const { data, error } = await supabase
      .from("product_runs")
      .select(runSelect)
      .eq("id", runId)
      .eq("project_id", projectId)
      .eq("organization_id", context.organization.id)
      .maybeSingle();

    if (error || !data) {
      throw productApiError("NOT_FOUND");
    }

    return success({ run: data });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
