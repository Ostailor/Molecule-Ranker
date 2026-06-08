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
