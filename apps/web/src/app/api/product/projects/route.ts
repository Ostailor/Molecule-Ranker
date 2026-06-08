import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { checkUsageAllowed, recordUsageEvent } from "@/lib/product/usage";
import { createClient } from "@/lib/supabase/server";

type ProjectInput = {
  name?: unknown;
  research_goal?: unknown;
  disease_focus?: unknown;
  target_focus?: unknown;
};

function textField(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function validateLength(value: string, field: string, maxLength: number) {
  if (value.length > maxLength) {
    throw productApiError("VALIDATION_ERROR", `${field} must be ${maxLength} characters or less.`);
  }
}

async function readProjectInput(request: Request): Promise<ProjectInput> {
  try {
    const body = await request.json();
    return typeof body === "object" && body !== null ? (body as ProjectInput) : {};
  } catch {
    throw productApiError("VALIDATION_ERROR", "Request body must be valid JSON.");
  }
}

export async function GET() {
  try {
    const supabase = await createClient();
    const context = await requireProductPermission("project:read", supabase);
    const { data, error } = await supabase
      .from("product_projects")
      .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
      .eq("organization_id", context.organization.id)
      .order("updated_at", { ascending: false })
      .limit(100);

    if (error) {
      throw productApiError("FORBIDDEN");
    }

    return success({
      projects: data ?? [],
    });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}

export async function POST(request: Request) {
  try {
    const supabase = await createClient();
    const context = await requireProductPermission("project:create", supabase);
    const input = await readProjectInput(request);
    const name = textField(input.name);
    const researchGoal = textField(input.research_goal);
    const diseaseFocus = textField(input.disease_focus);
    const targetFocus = textField(input.target_focus);

    if (!name) {
      throw productApiError("VALIDATION_ERROR", "Project name is required.");
    }

    validateLength(name, "Project name", 120);
    validateLength(researchGoal, "Research goal", 1000);
    validateLength(diseaseFocus, "Disease or research area", 160);
    validateLength(targetFocus, "Target focus", 160);
    await checkUsageAllowed("create_project", 1, { context, supabaseClient: supabase });

    const { data, error } = await supabase
      .from("product_projects")
      .insert({
        organization_id: context.organization.id,
        created_by_user_id: context.user.id,
        name,
        research_goal: researchGoal || null,
        disease_focus: diseaseFocus || null,
        target_focus: targetFocus || null,
        status: "active",
      })
      .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
      .single();

    if (error || !data) {
      throw productApiError("FORBIDDEN");
    }

    await recordUsageEvent("create_project", 1, { project_id: data.id }, { context, supabaseClient: supabase });

    return success({ project: data }, 201);
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
