"use client";

import { createBrowserClient } from "@supabase/ssr";

import { getSupabasePublicEnv, type Database } from "./types";

export function createClient() {
  const { publishableKey, url } = getSupabasePublicEnv();

  return createBrowserClient<Database>(url, publishableKey);
}
