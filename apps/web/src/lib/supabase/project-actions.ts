"use server";

import { redirect } from "next/navigation";

import { ProductApiError } from "@/lib/product/api-errors";
import { canCreateProject } from "@/lib/product/permissions";
import { checkUsageAllowed, recordUsageEvent } from "@/lib/product/usage";
import type { AuthActionState } from "./auth-action-state";
import { getCurrentUser } from "./auth";
import { createClient } from "./server";
import type { Membership, ProductRole } from "./types";

function fieldValue(formData: FormData, name: string) {
  return String(formData.get(name) ?? "").trim();
}

function actionError(message: string): AuthActionState {
  return {
    status: "error",
    message,
  };
}

function validateLength(value: string, field: string, maxLength: number) {
  if (value.length > maxLength) {
    return `${field} must be ${maxLength} characters or less.`;
  }

  return null;
}

async function getActiveMembership(userId: string) {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", userId)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();

  if (error) return { supabase, membership: null };

  return { supabase, membership: data as Membership | null };
}

export async function createProjectAction(
  previousState: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  void previousState;

  const user = await getCurrentUser();

  if (!user) {
    redirect("/login?next=/projects/new");
  }

  const { membership, supabase } = await getActiveMembership(user.id);

  if (!membership) {
    return actionError("Finish workspace setup before creating a project.");
  }

  const role = membership.role as ProductRole;

  if (!canCreateProject(role)) {
    return actionError("Your current role can view projects but cannot create them.");
  }

  try {
    await checkUsageAllowed("create_project", 1, { supabaseClient: supabase });
  } catch (error) {
    if (error instanceof ProductApiError && error.code === "PLAN_LIMIT_EXCEEDED") {
      return actionError(error.publicMessage);
    }

    return actionError("Could not verify current plan usage. Try again.");
  }

  const name = fieldValue(formData, "name");
  const researchGoal = fieldValue(formData, "research_goal");
  const diseaseFocus = fieldValue(formData, "disease_focus");
  const targetFocus = fieldValue(formData, "target_focus");

  if (!name) {
    return actionError("Project name is required.");
  }

  for (const message of [
    validateLength(name, "Project name", 120),
    validateLength(researchGoal, "Research goal", 1000),
    validateLength(diseaseFocus, "Disease or research area", 160),
    validateLength(targetFocus, "Target focus", 160),
  ]) {
    if (message) return actionError(message);
  }

  const { data, error } = await supabase
    .from("product_projects")
    .insert({
      organization_id: membership.organization_id,
      created_by_user_id: user.id,
      name,
      research_goal: researchGoal || null,
      disease_focus: diseaseFocus || null,
      target_focus: targetFocus || null,
      status: "active",
    })
    .select("id")
    .single();

  if (error || !data) {
    return actionError("Could not create the project. Review your workspace access and try again.");
  }

  try {
    await recordUsageEvent("create_project", 1, { project_id: data.id }, { supabaseClient: supabase });
  } catch {
    return actionError("The project was created, but usage recording did not complete. Refresh before creating another project.");
  }

  redirect(`/projects/${data.id}`);
}
