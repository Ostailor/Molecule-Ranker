import { Activity, FolderKanban, LifeBuoy, WalletCards } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  getPlanUsageLimits,
  getUsageSummaryForOrg,
  productUsageActionLabels,
  v02UsageActions,
  v03UsageActions,
} from "@/lib/product/usage";
import { productFeatureFlags } from "@/lib/product/feature-flags";
import { requireOrganizationMember } from "@/lib/product/auth-context";
import { createClient } from "@/lib/supabase/server";

function formatPlan(plan: string) {
  return plan.replace(/_/g, " ");
}

function formatLimit(limit: number | null) {
  if (limit === null) return "Unlimited";
  if (limit === 0) return "V0.3";
  if (limit >= 1_000_000) return "Internal";

  return String(limit);
}

export default async function UsagePage() {
  const supabase = await createClient();
  const context = await requireOrganizationMember(supabase);
  const usage = await getUsageSummaryForOrg(context.organization.id, { context, supabaseClient: supabase });
  const planLimits = getPlanUsageLimits(context.plan);
  const primaryActions = v02UsageActions.map((action) => usage.byAction[action]);
  const v03Actions = v03UsageActions.map((action) => usage.byAction[action]);

  return (
    <AppShell userRole={context.role}>
      <PageHeader
        title="Usage"
        description={`Current-period usage visible to your ${context.organization.name} membership. Owner/admin roles can review organization-wide rows.`}
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Metric label="Plan" value={formatPlan(context.plan)} detail="Configured on organization" icon={WalletCards} />
        <Metric
          label={productUsageActionLabels.create_project}
          value={String(usage.byAction.create_project.quantity)}
          detail={`${formatLimit(planLimits.create_project)} current-period limit`}
          icon={FolderKanban}
        />
        <Metric
          label={productUsageActionLabels.run_discovery}
          value={String(usage.byAction.run_discovery.quantity)}
          detail={`${formatLimit(planLimits.run_discovery)} current-period limit`}
          icon={Activity}
        />
        <Metric
          label={productUsageActionLabels.feedback_create}
          value={String(usage.byAction.feedback_create.quantity)}
          detail={`${formatLimit(planLimits.feedback_create)} current-period limit`}
          icon={LifeBuoy}
        />
        <Metric label="Usage events" value={String(usage.eventsThisMonth)} detail={`${usage.totalQuantityThisMonth} quantity units`} icon={Activity} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader title="Plan limits" eyebrow="V0.2 product actions" />
          <CardBody>
            <DataTable
              columns={["Action", "Used", "Limit", "Remaining"]}
              rows={primaryActions.map((summary) => [
                summary.label,
                String(summary.quantity),
                formatLimit(summary.limit),
                summary.remaining === null ? "Unlimited" : String(summary.remaining),
              ])}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Workspace scope" eyebrow={context.organization.name} action={<StatusBadge tone="teal">{context.role}</StatusBadge>} />
          <CardBody>
            <div className="grid gap-3">
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Organization</span>
                <span className="text-sm text-ink-700">{context.organization.name}</span>
              </div>
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Plan</span>
                <StatusBadge tone="teal">{formatPlan(context.plan)}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Period start</span>
                <span className="text-sm text-ink-700">{usage.periodStart.slice(0, 10)}</span>
              </div>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Discovery workflow usage" eyebrow="V0.3 run actions" />
          <CardBody>
            <DataTable
              columns={["Action", "Used", "Limit / availability"]}
              rows={v03Actions.map((summary) => [
                summary.label,
                String(summary.quantity),
                summary.placeholder ? (
                  <StatusBadge key={summary.action} tone="gray">
                    Later release
                  </StatusBadge>
                ) : (
                  formatLimit(summary.limit)
                ),
              ])}
            />
          </CardBody>
        </Card>

        {productFeatureFlags.stripeBillingPlaceholder ? (
          <Card>
            <CardHeader title="Billing" eyebrow="Release V0.5" />
            <CardBody>
              <div className="flex flex-wrap items-center gap-3">
                <StatusBadge tone="gray">No payment provider connected</StatusBadge>
                <StatusBadge tone="amber">Stripe planned for Release V0.5</StatusBadge>
              </div>
              <p className="mt-4 text-sm leading-6 text-ink-600">
                V0.3 records visible product usage for authenticated organization members. It does not enforce paid subscriptions,
                collect payment details, or create external workflow writes.
              </p>
            </CardBody>
          </Card>
        ) : null}
      </div>
    </AppShell>
  );
}
