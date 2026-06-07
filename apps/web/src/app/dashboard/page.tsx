import { Plus } from "lucide-react";
import { DashboardOverview, type DashboardViewState } from "@/components/dashboard/dashboard-overview";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { quickLinks } from "@/lib/routes";

type DashboardPageProps = {
  searchParams?: Promise<{
    state?: string;
  }>;
};

function dashboardState(value?: string): DashboardViewState {
  if (value === "empty" || value === "loading" || value === "error") return value;
  return "normal";
}

export default async function DashboardPage({ searchParams }: DashboardPageProps) {
  const params = await searchParams;
  const state = dashboardState(params?.state);

  return (
    <AppShell>
      <PageHeader
        title="Discovery dashboard"
        description="Monitor research hypotheses, candidate prioritization, result bundles, and review workload from synthetic mock data."
        actions={
          <>
            <Button href={quickLinks.newProject} variant="secondary" icon={Plus}>
              Create project
            </Button>
            <Button href={quickLinks.newRun} icon={Plus}>
              Start discovery run
            </Button>
          </>
        }
      />
      <DashboardOverview state={state} />
    </AppShell>
  );
}
