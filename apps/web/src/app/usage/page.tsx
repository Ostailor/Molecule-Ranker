import { BarChart3, DatabaseZap, Download, FlaskConical, FolderKanban, HardDrive, WalletCards } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Metric } from "@/components/ui/metric";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";
import { projects, resultBundles, usageSummary } from "@/lib/mock-data";
import { compactNumber } from "@/lib/formatting";

export default function UsagePage() {
  const exportedBundles = resultBundles.filter((bundle) => bundle.exportedAt).length;

  return (
    <AppShell>
      <PageHeader title="Usage" description="Mock workspace usage for planning capacity and review workload." />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Metric label="Plan placeholder" value="Pilot preview" detail="Release V0.1 mock workspace" icon={WalletCards} />
        <Metric label="Projects used" value={String(projects.length)} detail="Synthetic projects" icon={FolderKanban} />
        <Metric label="Runs used" value={String(usageSummary.discoveryRunsThisMonth)} detail={`${usageSummary.monthlyRunLimit} monthly preview limit`} icon={FlaskConical} />
        <Metric label="Generated hypotheses usage" value={String(usageSummary.generatedHypotheses)} detail="Research-planning placeholders" icon={BarChart3} />
        <Metric label="Exports usage" value={String(exportedBundles)} detail="Mock result bundle exports" icon={Download} />
        <Metric label="Storage usage" value="Demo only" detail={`${compactNumber(usageSummary.evidenceItemsReviewed)} evidence rows shown`} icon={HardDrive} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_0.9fr]">
        <Card>
          <CardHeader title="Billing placeholder" eyebrow="PLACEHOLDER_V0_1_USAGE" />
          <CardBody>
            <div className="flex flex-wrap items-center gap-3">
              <StatusBadge tone="gray">No billing connected</StatusBadge>
              <StatusBadge tone="amber">Payment provider integration coming in Release V0.5</StatusBadge>
            </div>
            <p className="mt-4 text-sm leading-6 text-ink-600">
              Usage values are static mock data. They validate layout and capacity messaging without requiring payments,
              authentication, or a live backend.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Usage notes" eyebrow="Synthetic data" />
          <CardBody>
            <div className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
              <DatabaseZap className="mt-0.5 h-4 w-4 shrink-0 text-teal-700" aria-hidden="true" />
              <p className="text-sm leading-6 text-ink-700">
                Counts are for UI review only. Real workspace metering, export storage, and plan enforcement are not
                implemented in this release.
              </p>
            </div>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
