import { RunSummary, type DiscoveryRunViewState } from "@/components/runs/run-summary";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, ProductRole, ProductRun, Project } from "@/lib/supabase/types";

type RunPageProps = {
  params: Promise<{ projectId: string; runId: string }>;
};

export default async function RunPage({ params }: RunPageProps) {
  const { projectId, runId } = await params;
  const user = await requireUser(`/login?next=/projects/${projectId}/runs/${runId}`);
  const supabase = await createClient();
  const { data: membershipData } = await supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  const membership = membershipData as Membership | null;
  const { data: projectData } = membership
    ? await supabase
        .from("product_projects")
        .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
        .eq("id", projectId)
        .eq("organization_id", membership.organization_id)
        .maybeSingle()
    : { data: null };
  const { data: runData } = membership
    ? await supabase
        .from("product_runs")
        .select("id, organization_id, project_id, created_by_user_id, run_type, mode, status, disease_or_goal, target_focus, options, progress, result_summary, error_summary, started_at, completed_at, created_at, updated_at")
        .eq("id", runId)
        .eq("project_id", projectId)
        .eq("organization_id", membership.organization_id)
        .maybeSingle()
    : { data: null };
  const project = projectData as Project | null;
  const run = runData as ProductRun | null;
  const runState = stateFromRunStatus(run?.status);
  const resultHref = `/projects/${projectId}/runs/${runId}/result`;

  if (!membership || !project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Discovery run not found"
          description="No accessible run matches this project in your active organization."
          actions={<Button href={`/projects/${projectId}`}>Back to project</Button>}
        />
        <Card>
          <CardHeader title="Unable to show run status" eyebrow="Tenant-scoped lookup" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a project and discovery run from your organization to inspect the bounded workflow timeline.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell userRole={membership.role as ProductRole}>
      <PageHeader
        title="Discovery run workspace"
        description="Inspect run status, coarse workflow steps, timestamps, reviewer warnings, and result bundle readiness."
        actions={
          runState === "completed" || runState === "partial" ? (
            <Button href={resultHref}>View result bundle</Button>
          ) : (
            <Button href={`/projects/${projectId}/runs/new`} variant="secondary">
              Start another run
            </Button>
          )
        }
      />
      <RunSummary run={run} runState={runState} projectName={project.name} resultHref={resultHref} />
    </AppShell>
  );
}

function stateFromRunStatus(status: string | undefined | null): DiscoveryRunViewState {
  if (status === "succeeded") return "completed";
  if (status === "partially_succeeded") return "partial";
  if (status === "running") return "running";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  return "queued";
}
