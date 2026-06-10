export type ProductRole = "owner" | "admin" | "researcher" | "viewer";

export type ProductPermission =
  | "project:create"
  | "project:read"
  | "project:update"
  | "run:create"
  | "run:read"
  | "candidate:save"
  | "export:create"
  | "feedback:create"
  | "admin:read"
  | "admin:manage_users";

export type ProductProfile = {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
  onboardingCompleted: boolean;
  researchUseAcknowledgedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ProductOrganization = {
  id: string;
  name: string;
  slug: string;
  ownerUserId: string | null;
  plan: "free_internal" | "pilot" | "trial";
  status: "active" | "suspended" | "archived";
  createdAt: string;
  updatedAt: string;
};

export type ProductMembership = {
  id: string;
  organizationId: string;
  userId: string;
  role: ProductRole;
  status: "active" | "invited" | "disabled" | "removed";
  createdAt: string;
  updatedAt: string;
};

export type ProductProject = {
  id: string;
  organizationId: string;
  createdByUserId: string | null;
  name: string;
  researchGoal: string | null;
  diseaseFocus: string | null;
  targetFocus: string | null;
  status: "active" | "archived" | "deleted";
  createdAt: string;
  updatedAt: string;
};

export type ProductUsageEvent = {
  id: string;
  organizationId: string;
  userId: string | null;
  eventType: string;
  quantity: number;
  metadata: Record<string, unknown>;
  createdAt: string;
};

export type ProductFeedback = {
  id: string;
  organizationId: string;
  userId: string | null;
  category: string;
  message: string;
  status: "open" | "in_review" | "resolved" | "closed";
  createdAt: string;
};

export type ProductRunStatus = "queued" | "running" | "succeeded" | "failed" | "partially_succeeded" | "cancelled";

export type ProductRunMode = "mocked" | "dry_run" | "read_only_live";

export type ProductRunOptions = {
  enableGeneration: boolean;
  maxGeneratedHypotheses: number;
  enableBiologics: boolean;
  enableAntibodyGeneration: boolean;
  enableStructure: boolean;
  enableCodexSummary: boolean;
  externalWrites: false;
  mode: ProductRunMode;
};

export type ProductRunProgress = {
  step: string;
  label?: string;
  percentComplete?: number;
  message?: string;
  updatedAt?: string;
};

export type ProductResultSummary = {
  status?: string;
  sections?: string[];
  candidateCount?: number;
  evidenceItemCount?: number;
  generatedHypothesisCount?: number;
  warningCount?: number;
  mode?: ProductRunMode;
  limitations?: string[];
};

export type ProductRun = {
  id: string;
  organizationId: string;
  projectId: string;
  createdByUserId: string | null;
  runType: "discovery" | "dry_run_discovery" | "mocked_discovery";
  mode: ProductRunMode;
  status: ProductRunStatus;
  diseaseOrGoal: string;
  targetFocus: string | null;
  options: ProductRunOptions;
  progress: ProductRunProgress;
  resultSummary: ProductResultSummary;
  errorSummary: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ProductRunArtifact = {
  id: string;
  organizationId: string;
  projectId: string;
  runId: string;
  artifactType: string;
  storageKind: "database" | "local_file" | "supabase_storage";
  storagePath: string | null;
  contentJson: Record<string, unknown> | null;
  contentText: string | null;
  sha256: string | null;
  sizeBytes: number | null;
  publicToUser: boolean;
  adminOnly: boolean;
  createdAt: string;
  metadata: Record<string, unknown>;
};

export type ProductAuthContext = {
  user: ProductProfile;
  organization: ProductOrganization;
  membership: ProductMembership;
  role: ProductRole;
  permissions: ProductPermission[];
};

export class ProductAuthorizationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProductAuthorizationError";
  }
}
