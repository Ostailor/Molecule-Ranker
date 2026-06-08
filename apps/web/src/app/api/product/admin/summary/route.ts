import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireAdminRole, sanitizeProductApiError } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

export async function GET() {
  try {
    const supabase = await createClient();
    const context = await requireAdminRole(supabase);
    const [projectsResult, membershipsResult, feedbackResult, usageResult] = await Promise.all([
      supabase
        .from("product_projects")
        .select("id", { count: "exact", head: true })
        .eq("organization_id", context.organization.id),
      supabase
        .from("product_memberships")
        .select("id", { count: "exact", head: true })
        .eq("organization_id", context.organization.id)
        .eq("status", "active"),
      supabase
        .from("product_feedback")
        .select("id", { count: "exact", head: true })
        .eq("organization_id", context.organization.id)
        .eq("status", "open"),
      supabase
        .from("product_usage_events")
        .select("id", { count: "exact", head: true })
        .eq("organization_id", context.organization.id),
    ]);

    if (projectsResult.error || membershipsResult.error || feedbackResult.error || usageResult.error) {
      throw productApiError("FORBIDDEN");
    }

    return success({
      summary: {
        organizationId: context.organization.id,
        organizationName: context.organization.name,
        plan: context.plan,
        projectCount: projectsResult.count ?? 0,
        activeMemberCount: membershipsResult.count ?? 0,
        openFeedbackCount: feedbackResult.count ?? 0,
        usageEventCount: usageResult.count ?? 0,
      },
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
