import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";
import type { ProductRun } from "@/lib/supabase/types";

type CancelRunRouteContext = {
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

export async function POST(_request: Request, { params }: CancelRunRouteContext) {
  try {
    const { projectId, runId } = await params;

    if (!isUuid(projectId) || !isUuid(runId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:create", supabase);
    const { data: currentData, error: currentError } = await supabase
      .from("product_runs")
      .select(runSelect)
      .eq("id", runId)
      .eq("project_id", projectId)
      .eq("organization_id", context.organization.id)
      .maybeSingle();

    if (currentError || !currentData) {
      throw productApiError("NOT_FOUND");
    }

    const currentRun = currentData as ProductRun;

    if (currentRun.status !== "queued" && currentRun.status !== "running") {
      return success({
        run: currentRun,
        cancellation: {
          supported: false,
          message: "Only queued or running discovery runs can be marked cancelled in V0.3.",
        },
      });
    }

    const { data, error } = await supabase
      .from("product_runs")
      .update({
        status: "cancelled",
        progress: { step: "cancelled" },
        error_summary: "Cancellation requested. Active subprocess termination is not implemented in the V0.3 dev runner.",
        completed_at: new Date().toISOString(),
      })
      .eq("id", runId)
      .eq("project_id", projectId)
      .eq("organization_id", context.organization.id)
      .select(runSelect)
      .single();

    if (error || !data) {
      throw productApiError("FORBIDDEN", "Could not cancel the discovery run.");
    }

    return success({
      run: data,
      cancellation: {
        supported: true,
        message: "Run marked cancelled. Active subprocess termination is not implemented in the V0.3 dev runner.",
      },
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
