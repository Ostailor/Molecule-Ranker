import { MessageSquareText, Plus } from "lucide-react";
import {
  DashboardAccountStatusPage,
  DashboardOverview,
  DashboardSetupIssuePage,
  type DashboardProject,
  type DashboardUsageSummary,
  type DashboardViewState,
} from "@/components/dashboard/dashboard-overview";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { quickLinks } from "@/lib/routes";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, Organization, ProductRole, Profile, Project, UsageEvent } from "@/lib/supabase/types";

type DashboardPageProps = {
  searchParams?: Promise<{
    state?: string;
  }>;
};

function dashboardState(value?: string): DashboardViewState {
  if (value === "empty" || value === "loading" || value === "error") return value;
  return "normal";
}

function displayNameFor(userEmail: string | undefined, profile: Profile | null) {
  return profile?.display_name || userEmail || "Researcher";
}

function planLabel(plan: string) {
  return plan.replace(/_/g, " ");
}

function monthStartIso() {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString();
}

function summarizeUsage(events: UsageEvent[]): DashboardUsageSummary {
  return events.reduce<DashboardUsageSummary>(
    (summary, event) => {
      const quantity = event.quantity ?? 1;
      summary.eventsThisMonth += 1;
      summary.totalQuantityThisMonth += quantity;

      if (event.event_type === "create_project") {
        summary.projectEventsThisMonth += quantity;
      }

      if (event.event_type === "feedback_create") {
        summary.feedbackEventsThisMonth += quantity;
      }

      return summary;
    },
    {
      eventsThisMonth: 0,
      totalQuantityThisMonth: 0,
      projectEventsThisMonth: 0,
      feedbackEventsThisMonth: 0,
    },
  );
}

function projectForDashboard(project: Project): DashboardProject {
  return {
    id: project.id,
    name: project.name,
    researchGoal: project.research_goal,
    diseaseFocus: project.disease_focus,
    targetFocus: project.target_focus,
    status: project.status,
    updatedAt: project.updated_at,
  };
}

export default async function DashboardPage({ searchParams }: DashboardPageProps) {
  const user = await requireUser("/login?next=/dashboard");
  const params = await searchParams;
  const state = dashboardState(params?.state);
  const supabase = await createClient();
  const profilePromise = supabase.from("product_profiles").select("*").eq("id", user.id).maybeSingle();
  const membershipPromise = supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  const [profileResult, membershipResult] = await Promise.all([profilePromise, membershipPromise]);
  const profile = profileResult.data as Profile | null;
  const membership = membershipResult.data as Membership | null;

  if (!membership) {
    return (
      <AppShell>
      <DashboardSetupIssuePage email={user.email ?? "current user"} />
      </AppShell>
    );
  }

  const { data: organizationData } = await supabase
    .from("product_organizations")
    .select("id, name, slug, owner_user_id, plan, status, created_at, updated_at")
    .eq("id", membership.organization_id)
    .maybeSingle();
  const organization = organizationData as Organization | null;

  if (!organization) {
    return (
      <AppShell>
      <DashboardSetupIssuePage email={user.email ?? "current user"} />
      </AppShell>
    );
  }

  if (organization.status !== "active") {
    return (
      <AppShell>
      <DashboardAccountStatusPage organizationName={organization.name} status={organization.status} />
      </AppShell>
    );
  }

  const projectsPromise = supabase
    .from("product_projects")
    .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
    .eq("organization_id", organization.id)
    .order("updated_at", { ascending: false })
    .limit(10);
  const usagePromise = supabase
    .from("product_usage_events")
    .select("id, organization_id, user_id, event_type, quantity, metadata, created_at")
    .eq("organization_id", organization.id)
    .gte("created_at", monthStartIso())
    .order("created_at", { ascending: false })
    .limit(100);
  const [projectsResult, usageResult] = await Promise.all([projectsPromise, usagePromise]);
  const projects = ((projectsResult.data ?? []) as Project[]).map(projectForDashboard);
  const usage = summarizeUsage((usageResult.data ?? []) as UsageEvent[]);

  return (
    <AppShell userRole={membership.role as ProductRole}>
      <PageHeader
        title="Discovery dashboard"
        description="Monitor your organization projects, usage, and pilot workflow placeholders from authenticated product data."
        actions={
          <>
            <Button href={quickLinks.newProject} variant="secondary" icon={Plus}>
              Create project
            </Button>
            <Button href="/feedback" icon={MessageSquareText}>
              Share feedback
            </Button>
          </>
        }
      />
      <DashboardOverview
        state={state}
        displayName={displayNameFor(user.email, profile)}
        organizationName={organization.name}
        role={membership.role as ProductRole}
        plan={planLabel(organization.plan)}
        projects={projects}
        usage={usage}
      />
    </AppShell>
  );
}
