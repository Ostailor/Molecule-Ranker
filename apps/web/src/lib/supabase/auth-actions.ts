"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { recordUsageEvent } from "@/lib/product/usage";
import type { AuthActionState } from "./auth-action-state";
import { getCurrentUser } from "./auth";
import { createClient } from "./server";

function fieldValue(formData: FormData, name: string) {
  return String(formData.get(name) ?? "").trim();
}

function safeRedirectPath(value: FormDataEntryValue | string | null | undefined, fallback = "/dashboard") {
  const raw = typeof value === "string" ? value : "";

  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return fallback;
  if (raw.includes("://")) return fallback;

  return raw;
}

function authError(message: string): AuthActionState {
  return {
    status: "error",
    message,
  };
}

function slugForOrganization(name: string) {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 42);
  const suffix = crypto.randomUUID().slice(0, 8);

  return `${base || "organization"}-${suffix}`;
}

async function productAppUrl() {
  if (process.env.PRODUCT_APP_URL) return process.env.PRODUCT_APP_URL;

  const headerStore = await headers();
  const host = headerStore.get("host");
  const proto = headerStore.get("x-forwarded-proto") ?? "http";

  return host ? `${proto}://${host}` : "http://localhost:3000";
}

export async function loginAction(previousState: AuthActionState, formData: FormData): Promise<AuthActionState> {
  void previousState;

  const email = fieldValue(formData, "email").toLowerCase();
  const password = fieldValue(formData, "password");
  const next = safeRedirectPath(formData.get("next"));

  if (!email || !password) {
    return authError("Enter both email and password.");
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({
    email,
    password,
  });

  if (error) {
    return authError("Could not sign in. Check your email and password.");
  }

  redirect(next);
}

export async function signupAction(previousState: AuthActionState, formData: FormData): Promise<AuthActionState> {
  void previousState;

  const email = fieldValue(formData, "email").toLowerCase();
  const password = fieldValue(formData, "password");
  const displayName = fieldValue(formData, "displayName");
  const organizationName = fieldValue(formData, "organizationName");
  const acknowledged = formData.get("researchUseAcknowledged") === "on";

  if (!email || !password || !displayName || !organizationName) {
    return authError("Complete all required account and workspace fields.");
  }

  if (password.length < 8) {
    return authError("Use a password with at least 8 characters.");
  }

  if (!acknowledged) {
    return authError("Acknowledge the research-use boundary before creating an account.");
  }

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      data: {
        display_name: displayName,
        organization_name: organizationName,
        research_use_acknowledged: true,
      },
      emailRedirectTo: `${await productAppUrl()}/auth/callback?next=/onboarding`,
    },
  });

  if (error || !data.user) {
    return authError("Could not create the account. Use a different email or try again later.");
  }

  if (!data.session) {
    return {
      status: "success",
      message: "Check your email to confirm the account, then sign in to finish workspace setup.",
    };
  }

  const now = new Date().toISOString();
  const profileResult = await supabase.from("product_profiles").insert({
    id: data.user.id,
    email,
    display_name: displayName,
    avatar_url: null,
    onboarding_completed: false,
    research_use_acknowledged_at: now,
  });

  if (profileResult.error) {
    return authError("Your account was created, but profile setup did not complete.");
  }

  const organizationResult = await supabase
    .from("product_organizations")
    .insert({
      name: organizationName,
      slug: slugForOrganization(organizationName),
      owner_user_id: data.user.id,
      plan: "free_internal",
      status: "active",
    })
    .select("id")
    .single();

  if (organizationResult.error || !organizationResult.data) {
    return authError("Your account was created, but organization setup did not complete.");
  }

  const membershipResult = await supabase.from("product_memberships").insert({
    organization_id: organizationResult.data.id,
    user_id: data.user.id,
    role: "owner",
    status: "active",
  });

  if (membershipResult.error) {
    return authError("Your account was created, but owner membership setup did not complete.");
  }

  redirect("/onboarding");
}

export async function forgotPasswordAction(
  previousState: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  void previousState;

  const email = fieldValue(formData, "email").toLowerCase();

  if (!email) {
    return authError("Enter the email for your account.");
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: `${await productAppUrl()}/auth/callback?next=/reset-password`,
  });

  if (error) {
    return authError("Could not send a password reset email.");
  }

  return {
    status: "success",
    message: "If an account exists for that email, a password reset link has been sent.",
  };
}

export async function resetPasswordAction(
  previousState: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  void previousState;

  const password = fieldValue(formData, "password");
  const confirmPassword = fieldValue(formData, "confirmPassword");

  if (!password || !confirmPassword) {
    return authError("Enter and confirm a new password.");
  }

  if (password.length < 8) {
    return authError("Use a password with at least 8 characters.");
  }

  if (password !== confirmPassword) {
    return authError("The password confirmation does not match.");
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({
    password,
  });

  if (error) {
    return authError("Open the latest reset link from your email before setting a new password.");
  }

  return {
    status: "success",
    message: "Password updated. You can now continue to the dashboard.",
  };
}

export async function finishOnboardingAction(
  previousState: AuthActionState,
  formData: FormData,
): Promise<AuthActionState> {
  void previousState;

  const user = await getCurrentUser();

  if (!user) {
    redirect("/login?next=/onboarding");
  }

  const displayName = fieldValue(formData, "displayName");
  const organizationName = fieldValue(formData, "organizationName");
  const useCase = fieldValue(formData, "useCase");
  const acknowledged = formData.get("researchUseAcknowledged") === "on";

  if (!displayName) {
    return authError("Confirm your display name before finishing onboarding.");
  }

  if (!acknowledged) {
    return authError("Acknowledge the research-use boundary before finishing onboarding.");
  }

  const allowedUseCases = new Set(["academic_researcher", "biotech_researcher", "independent_researcher", "founder", "other"]);

  if (!allowedUseCases.has(useCase)) {
    return authError("Choose the role or use case that best matches your pilot work.");
  }

  const supabase = await createClient();
  const now = new Date().toISOString();
  const email = user.email ?? "";
  const { data: existingProfile, error: profileReadError } = await supabase
    .from("product_profiles")
    .select("id")
    .eq("id", user.id)
    .maybeSingle();

  if (profileReadError) {
    return authError("Could not load your profile. Try again.");
  }

  const profileUpdatePayload = {
    display_name: displayName,
    onboarding_completed: true,
    research_use_acknowledged_at: now,
  };

  const profileResult = existingProfile
    ? await supabase.from("product_profiles").update(profileUpdatePayload).eq("id", user.id)
    : await supabase.from("product_profiles").insert({
        id: user.id,
        email,
        ...profileUpdatePayload,
        avatar_url: null,
      });

  if (profileResult.error) {
    return authError("Could not update your profile. Try again.");
  }

  const { data: existingMembership, error: membershipReadError } = await supabase
    .from("product_memberships")
    .select("organization_id, role, status")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();

  if (membershipReadError) {
    return authError("Could not load your organization membership. Try again.");
  }

  if (!existingMembership) {
    if (!organizationName) {
      return authError("Confirm or create an organization before finishing onboarding.");
    }

    const organizationResult = await supabase
      .from("product_organizations")
      .insert({
        name: organizationName,
        slug: slugForOrganization(organizationName),
        owner_user_id: user.id,
        plan: "free_internal",
        status: "active",
      })
      .select("id")
      .single();

    if (organizationResult.error || !organizationResult.data) {
      return authError("Could not create your organization. Try a different organization name.");
    }

    const membershipResult = await supabase.from("product_memberships").insert({
      organization_id: organizationResult.data.id,
      user_id: user.id,
      role: "owner",
      status: "active",
    });

    if (membershipResult.error) {
      return authError("Could not create your owner membership. Try again.");
    }
  }

  await supabase.auth.updateUser({
    data: {
      display_name: displayName,
      product_use_case: useCase,
      research_use_acknowledged: true,
    },
  });

  try {
    await recordUsageEvent("onboarding_complete", 1, { use_case: useCase }, { supabaseClient: supabase });
  } catch {
    // Usage recording must not block access after profile and membership setup have completed.
  }

  redirect("/dashboard");
}
