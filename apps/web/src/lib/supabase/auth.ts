import { redirect } from "next/navigation";

import { createClient } from "./server";
import type { CurrentUser, Profile, SessionClaims } from "./types";

export async function getSessionClaims(): Promise<SessionClaims | null> {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.getClaims();

  if (error) return null;

  return data?.claims ?? null;
}

export async function getCurrentUser(): Promise<CurrentUser | null> {
  const supabase = await createClient();
  const { data, error } = await supabase.auth.getUser();

  if (error) return null;

  return data.user;
}

export async function requireUser(redirectTo = "/login"): Promise<CurrentUser> {
  const user = await getCurrentUser();

  if (!user) {
    redirect(redirectTo);
  }

  return user;
}

export async function getCurrentProfile(): Promise<Profile | null> {
  const user = await getCurrentUser();

  if (!user) return null;

  const supabase = await createClient();
  const { data, error } = await supabase.from("product_profiles").select("*").eq("id", user.id).maybeSingle();

  if (error) return null;

  return data as Profile | null;
}

export async function signOut(): Promise<void> {
  const supabase = await createClient();
  await supabase.auth.signOut();
}
