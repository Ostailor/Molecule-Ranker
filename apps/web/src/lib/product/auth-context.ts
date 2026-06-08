import { canAccessAdmin, roleHasPermission } from "./permissions";
import type { ProductPermission } from "./types";
import { ProductApiError, productApiError } from "./api-errors";
import { createClient } from "@/lib/supabase/server";
import type { CurrentUser, Membership, Organization, ProductRole, Profile } from "@/lib/supabase/types";

export type ProductAuthSupabaseClient = Awaited<ReturnType<typeof createClient>>;

export type ProductRouteAuthContext = {
  user: CurrentUser;
  profile: Profile;
  organization: Organization;
  membership: Membership;
  role: ProductRole;
  plan: string;
};

async function resolveClient(supabaseClient?: ProductAuthSupabaseClient) {
  return supabaseClient ?? (await createClient());
}

function ensureActiveRole(value: string): ProductRole {
  if (value === "owner" || value === "admin" || value === "researcher" || value === "viewer") return value;

  throw productApiError("FORBIDDEN");
}

export async function requireProductUser(supabaseClient?: ProductAuthSupabaseClient) {
  const supabase = await resolveClient(supabaseClient);
  const { data, error } = await supabase.auth.getUser();

  if (error || !data.user) {
    throw productApiError("UNAUTHENTICATED");
  }

  return data.user;
}

export async function getActiveOrganizationForUser(userId: string, supabaseClient?: ProductAuthSupabaseClient) {
  const supabase = await resolveClient(supabaseClient);
  const { data: membershipData, error: membershipError } = await supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", userId)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();

  if (membershipError || !membershipData) {
    throw productApiError("ORGANIZATION_REQUIRED");
  }

  const membership = membershipData as Membership;
  const { data: organizationData, error: organizationError } = await supabase
    .from("product_organizations")
    .select("id, name, slug, owner_user_id, plan, status, created_at, updated_at")
    .eq("id", membership.organization_id)
    .maybeSingle();

  if (organizationError || !organizationData) {
    throw productApiError("ORGANIZATION_REQUIRED");
  }

  const organization = organizationData as Organization;

  if (organization.status !== "active") {
    throw productApiError("FORBIDDEN", "The organization is not active.");
  }

  return {
    membership,
    organization,
  };
}

export async function getProductAuthContext(supabaseClient?: ProductAuthSupabaseClient): Promise<ProductRouteAuthContext> {
  const supabase = await resolveClient(supabaseClient);
  const user = await requireProductUser(supabase);
  const { data: profileData, error: profileError } = await supabase
    .from("product_profiles")
    .select("id, email, display_name, avatar_url, onboarding_completed, research_use_acknowledged_at, created_at, updated_at")
    .eq("id", user.id)
    .maybeSingle();

  if (profileError || !profileData) {
    throw productApiError("ONBOARDING_REQUIRED");
  }

  const profile = profileData as Profile;

  if (!profile.onboarding_completed) {
    throw productApiError("ONBOARDING_REQUIRED");
  }

  const { membership, organization } = await getActiveOrganizationForUser(user.id, supabase);
  const role = ensureActiveRole(membership.role);

  return {
    user,
    profile,
    organization,
    membership,
    role,
    plan: organization.plan,
  };
}

export async function requireOrganizationMember(supabaseClient?: ProductAuthSupabaseClient) {
  return getProductAuthContext(supabaseClient);
}

export async function requireProductPermission(permission: ProductPermission, supabaseClient?: ProductAuthSupabaseClient) {
  const context = await getProductAuthContext(supabaseClient);

  if (!roleHasPermission(context.role, permission)) {
    throw productApiError("FORBIDDEN");
  }

  return context;
}

export async function requireAdminRole(supabaseClient?: ProductAuthSupabaseClient) {
  const context = await getProductAuthContext(supabaseClient);

  if (!canAccessAdmin(context.role)) {
    throw productApiError("FORBIDDEN");
  }

  return context;
}

export function sanitizeProductApiError(error: unknown) {
  if (error instanceof ProductApiError) return error;

  return productApiError("FORBIDDEN");
}
