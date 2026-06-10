import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StartDiscoveryRunForm } from "@/components/runs/start-discovery-run-form";
import { ProductApiError } from "@/lib/product/api-errors";
import { checkUsageAllowed } from "@/lib/product/usage";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, ProductRole, Project } from "@/lib/supabase/types";

type NewRunPageProps = {
  params: Promise<{ projectId: string }>;
};

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function booleanFromEnv(value: string | undefined) {
  return value === "1" || value === "true" || value === "TRUE";
}

export default async function NewRunPage({ params }: NewRunPageProps) {
  const { projectId } = await params;
  const user = await requireUser(`/login?next=/projects/${projectId}/runs/new`);

  if (!isUuid(projectId)) {
    return <RunSetupUnavailable projectId={projectId} />;
  }

  const supabase = await createClient();
  const { data: membershipData } = await supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  const membership = membershipData as Membership | null;

  if (!membership) {
    return <RunSetupUnavailable projectId={projectId} />;
  }

  const { data: projectData } = await supabase
    .from("product_projects")
    .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
    .eq("id", projectId)
    .eq("organization_id", membership.organization_id)
    .eq("status", "active")
    .maybeSingle();
  const project = projectData as Project | null;

  if (!project) {
    return <RunSetupUnavailable projectId={projectId} />;
  }

  let usageRemaining: number | null | undefined;
  let usageLimit: number | null | undefined;
  let usageBlockedMessage: string | null = null;

  try {
    const allowance = await checkUsageAllowed("run_discovery", 1, { supabaseClient: supabase });
    usageRemaining = allowance.remaining;
    usageLimit = allowance.limit;
  } catch (error) {
    usageBlockedMessage =
      error instanceof ProductApiError ? error.publicMessage : "Could not verify run usage limits for this organization.";
  }

  return (
    <AppShell userRole={membership.role as ProductRole}>
      <PageHeader
        title="Start discovery run"
        description="Configure a bounded discovery workflow for research hypothesis planning through the V0.3 product-safe runner."
      />
      <StartDiscoveryRunForm
        projectId={project.id}
        projectObjective={project.research_goal ?? ""}
        diseaseOrArea={project.disease_focus ?? "Research area"}
        targetFocus={project.target_focus}
        usageRemaining={usageRemaining}
        usageLimit={usageLimit}
        usageBlockedMessage={usageBlockedMessage}
        allowReadOnlyLive={booleanFromEnv(process.env.PRODUCT_READ_ONLY_LIVE_RUNS_ENABLED)}
      />
    </AppShell>
  );
}

function RunSetupUnavailable({ projectId }: { projectId: string }) {
  return (
    <AppShell>
      <PageHeader
        title="Project not found"
        description="No accessible active project matches this identifier."
        actions={<Button href="/dashboard">Back to dashboard</Button>}
      />
      <Card>
        <CardHeader title="Unable to start discovery run" eyebrow="Tenant-scoped lookup" />
        <CardBody>
          <p className="text-sm leading-6 text-ink-600">
            Create or select a project in your organization before configuring a bounded discovery workflow. No
            cross-organization project data is shown for {projectId}.
          </p>
        </CardBody>
      </Card>
    </AppShell>
  );
}
