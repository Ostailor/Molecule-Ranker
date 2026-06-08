import { BarChart3, CreditCard, FolderKanban, LifeBuoy, Lock, UserPlus, UsersRound } from "lucide-react";

import { ForbiddenPage } from "@/components/auth/forbidden-page";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import { requireAdminRole } from "@/lib/product/auth-context";
import { productServerFeatureFlagDefaults } from "@/lib/product/feature-flags";
import { createClient } from "@/lib/supabase/server";
import type { Membership, Profile, UsageEvent } from "@/lib/supabase/types";

type MemberRow = Pick<Membership, "id" | "organization_id" | "user_id" | "role" | "status" | "created_at" | "updated_at">;

function DisabledAdminAction({
  description,
  icon: Icon,
  label,
  status,
}: {
  description: string;
  icon: typeof UserPlus;
  label: string;
  status: string;
}) {
  return (
    <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-product bg-white text-teal-700">
            <Icon className="h-4 w-4" aria-hidden="true" />
          </span>
          <div>
            <p className="text-sm font-semibold text-ink-950">{label}</p>
            <p className="mt-1 text-sm leading-6 text-ink-600">{description}</p>
          </div>
        </div>
        <StatusBadge tone="gray">{status}</StatusBadge>
      </div>
    </div>
  );
}

function summarizeUsage(events: UsageEvent[]) {
  return events.reduce(
    (summary, event) => {
      summary.eventCount += 1;
      summary.quantity += event.quantity ?? 1;
      return summary;
    },
    {
      eventCount: 0,
      quantity: 0,
    },
  );
}

function featureFlagRows() {
  return [
    ["discoveryRunsPlaceholder", String(productServerFeatureFlagDefaults.discoveryRunsPlaceholder), "Discovery run placeholders only"],
    ["generatedHypothesesViewer", String(productServerFeatureFlagDefaults.generatedHypothesesViewer), "Generated hypothesis viewer remains bounded"],
    ["biologicsViewer", String(productServerFeatureFlagDefaults.biologicsViewer), "Biologics viewer hidden unless explicitly enabled"],
    ["antibodyGeneration", String(productServerFeatureFlagDefaults.antibodyGeneration), "Always disabled in Release V0.2"],
    ["externalIntegrations", String(productServerFeatureFlagDefaults.externalIntegrations), "External integrations remain hidden"],
    ["externalWrites", String(productServerFeatureFlagDefaults.externalWrites), "External writes remain disabled"],
    ["adminDashboard", String(productServerFeatureFlagDefaults.adminDashboard), "Role-gated owner/admin workspace surface"],
    ["stripeBilling", String(productServerFeatureFlagDefaults.stripeBilling), "Stripe integration hidden until V0.5"],
    ["exportsPlaceholder", String(productServerFeatureFlagDefaults.exportsPlaceholder), "Exports remain placeholder-only"],
  ];
}

export default async function AdminPage() {
  const supabase = await createClient();
  let context;

  try {
    context = await requireAdminRole(supabase);
  } catch {
    return <ForbiddenPage />;
  }

  const organizationId = context.organization.id;
  const [membershipsResult, projectsResult, feedbackResult, usageResult] = await Promise.all([
    supabase
      .from("product_memberships")
      .select("id, organization_id, user_id, role, status, created_at, updated_at")
      .eq("organization_id", organizationId)
      .order("created_at", { ascending: true }),
    supabase.from("product_projects").select("id", { count: "exact", head: true }).eq("organization_id", organizationId),
    supabase.from("product_feedback").select("id", { count: "exact", head: true }).eq("organization_id", organizationId),
    supabase
      .from("product_usage_events")
      .select("id, organization_id, user_id, event_type, quantity, metadata, created_at")
      .eq("organization_id", organizationId)
      .order("created_at", { ascending: false })
      .limit(100),
  ]);
  const memberships = (membershipsResult.data ?? []) as MemberRow[];
  const memberIds = memberships.map((membership) => membership.user_id);
  const profilesResult =
    memberIds.length > 0
      ? await supabase
          .from("product_profiles")
          .select("id, email, display_name, avatar_url, onboarding_completed, research_use_acknowledged_at, created_at, updated_at")
          .in("id", memberIds)
      : { data: [] };
  const profiles = new Map(((profilesResult.data ?? []) as Profile[]).map((profile) => [profile.id, profile]));
  const usageSummary = summarizeUsage((usageResult.data ?? []) as UsageEvent[]);

  return (
    <AppShell userRole={context.role}>
      <PageHeader
        title="Admin"
        description={`Owner/admin workspace controls for ${context.organization.name}. Data is scoped to the active organization only.`}
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric label="Members" value={String(memberships.length)} detail="Active workspace rows" icon={UsersRound} />
        <Metric label="Projects" value={String(projectsResult.count ?? 0)} detail="Current organization" icon={FolderKanban} />
        <Metric label="Usage events" value={String(usageSummary.eventCount)} detail={`${usageSummary.quantity} quantity units`} icon={BarChart3} />
        <Metric label="Feedback" value={String(feedbackResult.count ?? 0)} detail="Workspace feedback rows" icon={LifeBuoy} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader title="Members list" eyebrow={context.organization.name} action={<StatusBadge tone="teal">{context.role}</StatusBadge>} />
          <CardBody>
            <DataTable
              columns={["User", "Email", "Role", "Status"]}
              rows={memberships.map((membership) => {
                const profile = profiles.get(membership.user_id);

                return [
                  profile?.display_name ?? "Workspace member",
                  profile?.email ?? "Email unavailable",
                  <StatusBadge key={`${membership.id}-role`} tone={membership.role === "owner" ? "green" : membership.role === "admin" ? "teal" : "gray"}>
                    {membership.role}
                  </StatusBadge>,
                  <StatusBadge key={`${membership.id}-status`} tone={membership.status === "active" ? "green" : "gray"}>
                    {membership.status}
                  </StatusBadge>,
                ];
              })}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Workspace status" eyebrow="Organization" />
          <CardBody>
            <div className="grid gap-3">
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Organization</span>
                <span className="text-sm text-ink-700">{context.organization.name}</span>
              </div>
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Plan</span>
                <StatusBadge tone="teal">{context.plan}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Status</span>
                <StatusBadge tone={context.organization.status === "active" ? "green" : "amber"}>{context.organization.status}</StatusBadge>
              </div>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Feature flags" eyebrow="Static product config" />
          <CardBody>
            <DataTable
              columns={["Flag", "State", "Purpose"]}
              rows={featureFlagRows().map(([flag, state, purpose]) => [
                flag,
                <StatusBadge key={flag} tone={state === "true" ? "green" : "gray"}>
                  {state}
                </StatusBadge>,
                purpose,
              ])}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Admin actions" eyebrow="Later releases" />
          <CardBody className="space-y-3">
            <DisabledAdminAction icon={UserPlus} label="Invite user" status="Disabled" description="Invitations are planned for a later release." />
            <DisabledAdminAction icon={Lock} label="Manage roles" status="Disabled" description="Role changes are intentionally disabled in V0.2." />
            {productServerFeatureFlagDefaults.stripeBillingPlaceholder ? (
              <DisabledAdminAction icon={CreditCard} label="Billing" status="V0.5" description="Billing and Stripe are planned for Release V0.5." />
            ) : null}
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
