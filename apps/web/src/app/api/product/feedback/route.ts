import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { checkUsageAllowed, recordUsageEvent } from "@/lib/product/usage";
import { createClient } from "@/lib/supabase/server";

type FeedbackInput = {
  category?: unknown;
  message?: unknown;
};

function textField(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

async function readFeedbackInput(request: Request): Promise<FeedbackInput> {
  try {
    const body = await request.json();
    return typeof body === "object" && body !== null ? (body as FeedbackInput) : {};
  } catch {
    throw productApiError("VALIDATION_ERROR", "Request body must be valid JSON.");
  }
}

export async function POST(request: Request) {
  try {
    const supabase = await createClient();
    const context = await requireProductPermission("feedback:create", supabase);
    const input = await readFeedbackInput(request);
    const category = textField(input.category);
    const message = textField(input.message);

    if (!category || !message) {
      throw productApiError("VALIDATION_ERROR", "Feedback category and message are required.");
    }

    if (category.length > 80) {
      throw productApiError("VALIDATION_ERROR", "Feedback category must be 80 characters or less.");
    }

    if (message.length > 4000) {
      throw productApiError("VALIDATION_ERROR", "Feedback message must be 4000 characters or less.");
    }

    await checkUsageAllowed("feedback_create", 1, { context, supabaseClient: supabase });

    const { data, error } = await supabase
      .from("product_feedback")
      .insert({
        organization_id: context.organization.id,
        user_id: context.user.id,
        category,
        message,
        status: "open",
      })
      .select("id, organization_id, user_id, category, message, status, created_at")
      .single();

    if (error || !data) {
      throw productApiError("FORBIDDEN");
    }

    await recordUsageEvent("feedback_create", 1, { category, feedback_id: data.id }, { context, supabaseClient: supabase });

    return success({ feedback: data }, 201);
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
