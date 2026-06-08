import { failure, success } from "@/lib/product/api-response";
import { getProductAuthContext, sanitizeProductApiError } from "@/lib/product/auth-context";

export async function GET() {
  try {
    const context = await getProductAuthContext();

    return success({
      user: {
        id: context.user.id,
        email: context.user.email ?? null,
      },
      profile: context.profile,
      organization: context.organization,
      membership: context.membership,
      role: context.role,
      plan: context.plan,
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
