import type { User } from "@supabase/supabase-js";

export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

export type ProductRole = "owner" | "admin" | "researcher" | "viewer";

export type Profile = {
  id: string;
  email: string | null;
  display_name: string | null;
  avatar_url: string | null;
  onboarding_completed: boolean;
  research_use_acknowledged_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Organization = {
  id: string;
  name: string;
  slug: string;
  owner_user_id: string | null;
  plan: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Membership = {
  id: string;
  organization_id: string;
  user_id: string;
  role: ProductRole;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  organization_id: string;
  created_by_user_id: string | null;
  name: string;
  research_goal: string | null;
  disease_focus: string | null;
  target_focus: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type UsageEvent = {
  id: string;
  organization_id: string;
  user_id: string | null;
  event_type: string;
  quantity: number;
  metadata: Json;
  created_at: string;
};

export type ProductFeedback = {
  id: string;
  organization_id: string;
  user_id: string | null;
  category: string;
  message: string;
  status: string;
  created_at: string;
};

export type Database = {
  public: {
    Tables: {
      product_profiles: {
        Row: Profile;
        Insert: Omit<Profile, "created_at" | "updated_at"> & {
          created_at?: string;
          updated_at?: string;
        };
        Update: Partial<Omit<Profile, "id">>;
        Relationships: [];
      };
      product_organizations: {
        Row: Organization;
        Insert: Omit<Organization, "id" | "created_at" | "updated_at"> & {
          id?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: Partial<Omit<Organization, "id">>;
        Relationships: [];
      };
      product_memberships: {
        Row: Membership;
        Insert: Omit<Membership, "id" | "created_at" | "updated_at"> & {
          id?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: Partial<Omit<Membership, "id">>;
        Relationships: [];
      };
      product_projects: {
        Row: Project;
        Insert: Omit<Project, "id" | "created_at" | "updated_at"> & {
          id?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: Partial<Omit<Project, "id">>;
        Relationships: [];
      };
      product_usage_events: {
        Row: UsageEvent;
        Insert: Omit<UsageEvent, "id" | "created_at"> & {
          id?: string;
          created_at?: string;
          quantity?: number;
          metadata?: Json;
        };
        Update: Partial<Omit<UsageEvent, "id">>;
        Relationships: [];
      };
      product_feedback: {
        Row: ProductFeedback;
        Insert: Omit<ProductFeedback, "id" | "created_at"> & {
          id?: string;
          created_at?: string;
        };
        Update: Partial<Omit<ProductFeedback, "id">>;
        Relationships: [];
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: {
      product_role: ProductRole;
    };
    CompositeTypes: Record<string, never>;
  };
};

export type CurrentUser = User;
export type SessionClaims = {
  sub?: string;
  email?: string;
  role?: string;
  aud?: string | string[];
  exp?: number;
  iat?: number;
  app_metadata?: {
    role?: string;
    [key: string]: Json | undefined;
  };
  user_metadata?: {
    role?: string;
    [key: string]: Json | undefined;
  };
  [key: string]: Json | undefined;
};

export type SupabasePublicEnv = {
  url: string;
  publishableKey: string;
};

export class SupabaseEnvError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SupabaseEnvError";
  }
}

export function getSupabasePublicEnv(): SupabasePublicEnv {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  const missing = [
    ["NEXT_PUBLIC_SUPABASE_URL", url],
    ["NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", publishableKey],
  ]
    .filter(([, value]) => !value)
    .map(([name]) => name);

  if (missing.length > 0) {
    throw new SupabaseEnvError(`Missing required Supabase environment variable(s): ${missing.join(", ")}`);
  }

  if (!url || !publishableKey) {
    throw new SupabaseEnvError("Supabase public environment validation failed.");
  }

  return {
    url,
    publishableKey,
  };
}
