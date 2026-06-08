import { ArrowLeft, FileArchive, FlaskConical, ShieldAlert, Star } from "lucide-react";

import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import { dateLabel } from "@/lib/formatting";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, ProductRole, Project } from "@/lib/supabase/types";

type ProjectPageProps = {
  params: Promise<{
    projectId: string;
  }>;
};

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function ProjectNotFound({ projectId }: { projectId: string }) {
  return (
    <AppShell>
      <PageHeader title="Project not found" description="No accessible project matches this identifier." />
      <Card>
        <CardBody className="grid gap-5 p-6 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <h2 className="text-lg font-semibold text-ink-950">Project is unavailable</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              The project may not exist, may belong to another organization, or may no longer be active. No cross-organization
              project data is shown for {projectId}.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button href="/dashboard" variant="secondary" icon={ArrowLeft}>
              Back to dashboard
            </Button>
            <Button href="/projects/new">Create project</Button>
          </div>
        </CardBody>
      </Card>
    </AppShell>
  );
}

function SetupIssuePage() {
  return (
    <AppShell>
      <PageHeader title="Project unavailable" description="An active organization membership is required to view projects." />
      <Card>
        <CardBody className="grid gap-5 p-6 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <h2 className="text-lg font-semibold text-ink-950">Workspace membership required</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              Finish onboarding or contact a workspace owner before opening project details.
            </p>
          </div>
          <Button href="/onboarding">Finish onboarding</Button>
        </CardBody>
      </Card>
    </AppShell>
  );
}

function ProjectSummary({ project }: { project: Project }) {
  return (
    <Card>
      <CardHeader title="Project summary" eyebrow="Product data" action={<StatusBadge tone="teal">{project.status}</StatusBadge>} />
      <CardBody className="space-y-4">
        <p className="text-sm leading-6 text-ink-600">{project.research_goal ?? "No research goal has been added yet."}</p>
        <div className="grid gap-3 sm:grid-cols-4">
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Disease or area</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">{project.disease_focus ?? "Not specified"}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Target focus</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">{project.target_focus ?? "Not specified"}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Updated</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">{dateLabel(project.updated_at)}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Created by</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">{project.created_by_user_id ? "Workspace member" : "Unknown"}</p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function ProjectUsagePlaceholder() {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Metric label="Project runs" value="0" detail="Connected in V0.3" icon={FlaskConical} />
      <Metric label="Candidate rows" value="0" detail="Placeholder until workflow connection" icon={Star} />
      <Metric label="Result bundles" value="0" detail="Placeholder until V0.3/V0.4" icon={FileArchive} />
    </div>
  );
}

function RunsPlaceholder() {
  return (
    <Card>
      <CardHeader title="Recent runs" eyebrow="Placeholder until V0.3" action={<StatusBadge tone="gray">Disabled</StatusBadge>} />
      <CardBody className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <h2 className="text-lg font-semibold text-ink-950">No discovery runs yet</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
            Start new discovery run remains disabled until Release V0.3 connects the workflow. No live workflow execution is started in V0.2.
          </p>
        </div>
        <StatusBadge tone="gray">V0.3</StatusBadge>
      </CardBody>
    </Card>
  );
}

function ResultBundlesPlaceholder() {
  return (
    <Card>
      <CardHeader title="Result bundles" eyebrow="Placeholder until V0.3/V0.4" action={<StatusBadge tone="gray">Disabled</StatusBadge>} />
      <CardBody>
        <p className="text-sm leading-6 text-ink-600">
          Result bundles will summarize completed discovery workflows in a later release. V0.2 only shows the project record
          and does not expose raw internal execution details.
        </p>
      </CardBody>
    </Card>
  );
}

function SafetyBoundary() {
  return (
    <Card>
      <CardBody className="flex gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" aria-hidden="true" />
        <div>
          <h2 className="text-sm font-semibold text-ink-950">Project safety boundary</h2>
          <p className="mt-2 text-sm leading-6 text-ink-700">
            Do not add patient-specific or protected health information to project fields. Project outputs are research-planning
            artifacts only and require expert review.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

export default async function ProjectPage({ params }: ProjectPageProps) {
  const user = await requireUser("/login");
  const { projectId } = await params;

  if (!isUuid(projectId)) return <ProjectNotFound projectId={projectId} />;

  const supabase = await createClient();
  const { data: membershipData } = await supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  const membership = membershipData as Membership | null;

  if (!membership) return <SetupIssuePage />;

  const { data: projectData } = await supabase
    .from("product_projects")
    .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
    .eq("id", projectId)
    .eq("organization_id", membership.organization_id)
    .maybeSingle();
  const project = projectData as Project | null;

  if (!project) return <ProjectNotFound projectId={projectId} />;

  return (
    <AppShell userRole={membership.role as ProductRole}>
      <PageHeader
        title={project.name}
        description={project.research_goal ?? "Project details are scoped to your active organization membership."}
        actions={
          <Button href="/dashboard" variant="secondary" icon={ArrowLeft}>
            Back to dashboard
          </Button>
        }
      />
      <div className="space-y-6">
        <ProjectSummary project={project} />
        <ProjectUsagePlaceholder />
        <RunsPlaceholder />
        <ResultBundlesPlaceholder />
        <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
          <SafetyBoundary />
          <Card>
            <CardHeader title="Research-use reminder" eyebrow="Boundary" />
            <CardBody>
              <ResearchUseBanner compact />
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
