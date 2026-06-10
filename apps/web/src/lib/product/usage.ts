import { productApiError } from "./api-errors";
import { requireOrganizationMember, type ProductAuthSupabaseClient, type ProductRouteAuthContext } from "./auth-context";
import { createClient } from "@/lib/supabase/server";
import type { Json, UsageEvent } from "@/lib/supabase/types";

export const v02UsageActions = ["create_project", "feedback_create", "login", "onboarding_complete"] as const;

export const v03UsageActions = ["run_discovery", "generated_hypotheses", "export_result", "codex_task"] as const;

export const productUsageActions = [...v02UsageActions, ...v03UsageActions] as const;

export type ProductUsageAction = (typeof productUsageActions)[number];

export type ProductUsagePlanLimits = Record<ProductUsageAction, number | null>;

export type ProductUsagePlanConfig = Record<string, ProductUsagePlanLimits>;

export type ProductUsageMetadata = Record<string, Json | undefined>;

export type ProductUsageActionSummary = {
  action: ProductUsageAction;
  label: string;
  quantity: number;
  eventCount: number;
  limit: number | null;
  remaining: number | null;
  placeholder: boolean;
};

export type ProductUsageSummary = {
  organizationId: string;
  plan: string;
  periodStart: string;
  generatedAt: string;
  eventsThisMonth: number;
  totalQuantityThisMonth: number;
  byAction: Record<ProductUsageAction, ProductUsageActionSummary>;
};

export type ProductUsageAllowance = {
  action: ProductUsageAction;
  allowed: true;
  plan: string;
  used: number;
  limit: number | null;
  remaining: number | null;
  requestedQuantity: number;
};

type ProductUsageOptions = {
  context?: ProductRouteAuthContext;
  limits?: ProductUsagePlanConfig;
  periodStart?: string;
  supabaseClient?: ProductAuthSupabaseClient;
};

const HIGH_INTERNAL_LIMIT = 1_000_000;

export const productUsageActionLabels: Record<ProductUsageAction, string> = {
  create_project: "Create projects",
  feedback_create: "Feedback submissions",
  login: "Logins",
  onboarding_complete: "Onboarding completions",
  run_discovery: "Discovery runs",
  generated_hypotheses: "Generated hypotheses",
  export_result: "Result exports",
  codex_task: "Codex tasks",
};

export const defaultProductUsagePlanLimits: ProductUsagePlanConfig = {
  free_internal: {
    create_project: HIGH_INTERNAL_LIMIT,
    feedback_create: HIGH_INTERNAL_LIMIT,
    login: HIGH_INTERNAL_LIMIT,
    onboarding_complete: HIGH_INTERNAL_LIMIT,
    run_discovery: HIGH_INTERNAL_LIMIT,
    generated_hypotheses: 0,
    export_result: 0,
    codex_task: 0,
  },
  pilot: {
    create_project: 10,
    feedback_create: 100,
    login: 500,
    onboarding_complete: 1,
    run_discovery: 50,
    generated_hypotheses: 0,
    export_result: 0,
    codex_task: 0,
  },
  trial: {
    create_project: 3,
    feedback_create: 25,
    login: 100,
    onboarding_complete: 1,
    run_discovery: 3,
    generated_hypotheses: 0,
    export_result: 0,
    codex_task: 0,
  },
};

export function monthStartIso(date = new Date()) {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)).toISOString();
}

export function isProductUsageAction(action: string): action is ProductUsageAction {
  return productUsageActions.includes(action as ProductUsageAction);
}

export function getPlanUsageLimits(plan: string, config: ProductUsagePlanConfig = defaultProductUsagePlanLimits) {
  return config[plan] ?? config.free_internal;
}

async function resolveContext(options: ProductUsageOptions) {
  return options.context ?? (await requireOrganizationMember(options.supabaseClient));
}

async function resolveClient(options: ProductUsageOptions) {
  return options.supabaseClient ?? (await createClient());
}

function emptyActionSummary(action: ProductUsageAction, limit: number | null): ProductUsageActionSummary {
  return {
    action,
    label: productUsageActionLabels[action],
    quantity: 0,
    eventCount: 0,
    limit,
    remaining: limit,
    placeholder: action !== "run_discovery" && v03UsageActions.includes(action as (typeof v03UsageActions)[number]),
  };
}

function usageSummaryFromEvents({
  events,
  organizationId,
  periodStart,
  plan,
  planLimits,
}: {
  events: UsageEvent[];
  organizationId: string;
  periodStart: string;
  plan: string;
  planLimits: ProductUsagePlanLimits;
}): ProductUsageSummary {
  const byAction = Object.fromEntries(
    productUsageActions.map((action) => [action, emptyActionSummary(action, planLimits[action])]),
  ) as Record<ProductUsageAction, ProductUsageActionSummary>;

  for (const event of events) {
    if (!isProductUsageAction(event.event_type)) continue;

    const quantity = event.quantity ?? 1;
    const summary = byAction[event.event_type];
    summary.quantity += quantity;
    summary.eventCount += 1;
  }

  for (const action of productUsageActions) {
    const summary = byAction[action];
    summary.remaining = summary.limit === null ? null : Math.max(summary.limit - summary.quantity, 0);
  }

  return {
    organizationId,
    plan,
    periodStart,
    generatedAt: new Date().toISOString(),
    eventsThisMonth: events.length,
    totalQuantityThisMonth: events.reduce((total, event) => total + (event.quantity ?? 1), 0),
    byAction,
  };
}

export async function getUsageSummaryForOrg(orgId: string, options: ProductUsageOptions = {}) {
  const context = await resolveContext(options);

  if (context.organization.id !== orgId) {
    throw productApiError("FORBIDDEN");
  }

  const periodStart = options.periodStart ?? monthStartIso();
  const supabase = await resolveClient(options);
  const { data, error } = await supabase
    .from("product_usage_events")
    .select("id, organization_id, user_id, event_type, quantity, metadata, created_at")
    .eq("organization_id", orgId)
    .gte("created_at", periodStart)
    .order("created_at", { ascending: false })
    .limit(500);

  if (error) {
    throw productApiError("FORBIDDEN");
  }

  return usageSummaryFromEvents({
    events: (data ?? []) as UsageEvent[],
    organizationId: orgId,
    periodStart,
    plan: context.plan,
    planLimits: getPlanUsageLimits(context.plan, options.limits),
  });
}

export async function checkUsageAllowed(
  action: ProductUsageAction,
  quantity = 1,
  options: ProductUsageOptions = {},
): Promise<ProductUsageAllowance> {
  const context = await resolveContext(options);
  const summary = await getUsageSummaryForOrg(context.organization.id, {
    ...options,
    context,
  });
  const actionSummary = summary.byAction[action];
  const limit = actionSummary.limit;
  const requestedQuantity = Math.max(1, Math.floor(quantity));
  const projectedQuantity = actionSummary.quantity + requestedQuantity;

  if (limit !== null && projectedQuantity > limit) {
    const label = productUsageActionLabels[action];
    const message =
      limit === 0
        ? `${label} is not available in Release V0.3 for the ${summary.plan} plan.`
        : `${label} exceeds the ${summary.plan} plan limit of ${limit} for the current period.`;

    throw productApiError("PLAN_LIMIT_EXCEEDED", message);
  }

  return {
    action,
    allowed: true,
    plan: summary.plan,
    used: actionSummary.quantity,
    limit,
    remaining: limit === null ? null : Math.max(limit - projectedQuantity, 0),
    requestedQuantity,
  };
}

export async function recordUsageEvent(
  action: ProductUsageAction,
  quantity = 1,
  metadata: ProductUsageMetadata = {},
  options: ProductUsageOptions = {},
) {
  const context = await resolveContext(options);
  const requestedQuantity = Math.max(1, Math.floor(quantity));
  await checkUsageAllowed(action, requestedQuantity, {
    ...options,
    context,
  });

  const supabase = await resolveClient(options);
  const { data, error } = await supabase
    .from("product_usage_events")
    .insert({
      organization_id: context.organization.id,
      user_id: context.user.id,
      event_type: action,
      quantity: requestedQuantity,
      metadata: metadata as Json,
    })
    .select("id, organization_id, user_id, event_type, quantity, metadata, created_at")
    .single();

  if (error || !data) {
    throw productApiError("FORBIDDEN");
  }

  return data as UsageEvent;
}
