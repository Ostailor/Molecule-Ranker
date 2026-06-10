import { ResultBundleOverview } from "@/components/runs/result-bundle-overview";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, ProductRole, ProductRun, Project } from "@/lib/supabase/types";

type ResultPageProps = {
  params: Promise<{ projectId: string; runId: string }>;
};

export default async function ResultPage({ params }: ResultPageProps) {
  const { projectId, runId } = await params;
  const user = await requireUser(`/login?next=/projects/${projectId}/runs/${runId}/result`);
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

  if (!membership || !project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Result bundle not found"
          description="No accessible result bundle matches this route."
          actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run</Button>}
        />
        <Card>
          <CardHeader title="Unable to show result bundle" eyebrow="Tenant-scoped lookup" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a completed discovery run from your organization to inspect the result summary, limitations, and
              review checklist.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell userRole={membership.role as ProductRole}>
      <PageHeader
        title="Result bundle"
        description="Review candidate ranking, evidence coverage, generated hypotheses, limitations, guardrail notices, and export readiness."
        actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run status</Button>}
      />
      <ResultBundleOverview project={project} initialRun={run} projectId={projectId} runId={runId} />
    </AppShell>
  );
}
