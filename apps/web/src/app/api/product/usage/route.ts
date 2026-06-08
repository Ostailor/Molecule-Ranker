import { failure, success } from "@/lib/product/api-response";
import { requireOrganizationMember, sanitizeProductApiError } from "@/lib/product/auth-context";
import { getUsageSummaryForOrg } from "@/lib/product/usage";
import { createClient } from "@/lib/supabase/server";

export async function GET() {
  try {
    const supabase = await createClient();
    const context = await requireOrganizationMember(supabase);
    const usage = await getUsageSummaryForOrg(context.organization.id, { context, supabaseClient: supabase });

    return success({
      usage,
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
