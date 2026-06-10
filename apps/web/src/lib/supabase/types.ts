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

export type ProductRunStatus = "queued" | "running" | "succeeded" | "failed" | "partially_succeeded" | "cancelled";
export type ProductRunMode = "mocked" | "dry_run" | "read_only_live";
export type ProductRunType = "discovery" | "dry_run_discovery" | "mocked_discovery";
export type ProductRunArtifactStorageKind = "database" | "local_file" | "supabase_storage";

export type ProductRun = {
  id: string;
  organization_id: string;
  project_id: string;
  created_by_user_id: string | null;
  run_type: ProductRunType;
  mode: ProductRunMode;
  status: ProductRunStatus;
  disease_or_goal: string;
  target_focus: string | null;
  options: Json;
  progress: Json;
  result_summary: Json;
  error_summary: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ProductRunArtifact = {
  id: string;
  organization_id: string;
  project_id: string;
  run_id: string;
  artifact_type: string;
  storage_kind: ProductRunArtifactStorageKind;
  storage_path: string | null;
  content_json: Json | null;
  content_text: string | null;
  sha256: string | null;
  size_bytes: number | null;
  public_to_user: boolean;
  admin_only: boolean;
  created_at: string;
  metadata: Json;
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
      product_runs: {
        Row: ProductRun;
        Insert: Omit<
          ProductRun,
          | "id"
          | "created_at"
          | "updated_at"
          | "run_type"
          | "status"
          | "mode"
          | "options"
          | "progress"
          | "result_summary"
          | "error_summary"
          | "started_at"
          | "completed_at"
        > & {
          id?: string;
          created_at?: string;
          updated_at?: string;
          run_type?: ProductRunType;
          status?: ProductRunStatus;
          mode?: ProductRunMode;
          options?: Json;
          progress?: Json;
          result_summary?: Json;
          error_summary?: string | null;
          started_at?: string | null;
          completed_at?: string | null;
        };
        Update: Partial<Omit<ProductRun, "id" | "organization_id" | "project_id" | "created_by_user_id">>;
        Relationships: [];
      };
      product_run_artifacts: {
        Row: ProductRunArtifact;
        Insert: Omit<
          ProductRunArtifact,
          | "id"
          | "created_at"
          | "storage_kind"
          | "storage_path"
          | "content_json"
          | "content_text"
          | "sha256"
          | "size_bytes"
          | "public_to_user"
          | "admin_only"
          | "metadata"
        > & {
          id?: string;
          created_at?: string;
          storage_kind?: ProductRunArtifactStorageKind;
          storage_path?: string | null;
          content_json?: Json | null;
          content_text?: string | null;
          sha256?: string | null;
          size_bytes?: number | null;
          public_to_user?: boolean;
          admin_only?: boolean;
          metadata?: Json;
        };
        Update: Partial<Omit<ProductRunArtifact, "id" | "organization_id" | "project_id" | "run_id">>;
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
