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
import type { Membership, ProductRole, ProductRun, ProductRunArtifact, Project } from "@/lib/supabase/types";

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

function ProjectUsageSummary({ runs, artifacts }: { runs: ProductRun[]; artifacts: ProductRunArtifact[] }) {
  const completedRuns = runs.filter((run) => run.status === "succeeded").length;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Metric label="Project runs" value={String(runs.length)} detail={`${completedRuns} completed`} icon={FlaskConical} />
      <Metric label="Candidate rows" value="Summary only" detail="Detailed UI later" icon={Star} />
      <Metric label="Result bundles" value={String(artifacts.length)} detail="Product-safe artifacts" icon={FileArchive} />
    </div>
  );
}

function RecentRuns({ projectId, runs }: { projectId: string; runs: ProductRun[] }) {
  return (
    <Card>
      <CardHeader
        title="Recent runs"
        eyebrow="V0.3 product state"
        action={<Button href={`/projects/${projectId}/runs/new`}>Start discovery run</Button>}
      />
      <CardBody>
        {runs.length > 0 ? (
          <div className="grid gap-3">
            {runs.map((run) => (
              <div key={run.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <a href={`/projects/${projectId}/runs/${run.id}`} className="focus-ring font-semibold text-ink-950 hover:text-teal-700">
                    {run.disease_or_goal}
                  </a>
                  <StatusBadge tone={runStatusTone(run.status)}>{runStatusLabel(run.status)}</StatusBadge>
                </div>
                <div className="mt-3 grid gap-2 text-xs leading-5 text-ink-500 sm:grid-cols-3">
                  <span>Mode: {run.mode}</span>
                  <span>Created: {dateLabel(run.created_at)}</span>
                  <span>Completed: {run.completed_at ? dateLabel(run.completed_at) : "Pending"}</span>
                </div>
                {hasResultBundle(run) ? (
                  <a href={`/projects/${projectId}/runs/${run.id}/result`} className="mt-3 inline-flex text-sm font-semibold text-teal-700 hover:text-teal-550">
                    View result bundle
                  </a>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <div className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <h2 className="text-lg font-semibold text-ink-950">No discovery runs yet</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
                Start a bounded dry-run or mocked workflow from this project. V0.3 stores product-safe status and result
                artifacts only.
              </p>
            </div>
            <StatusBadge tone="teal">Ready</StatusBadge>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function runStatusTone(status: ProductRun["status"]): "green" | "teal" | "amber" | "rose" | "gray" {
  if (status === "succeeded") return "green";
  if (status === "failed") return "rose";
  if (status === "partially_succeeded") return "amber";
  if (status === "cancelled") return "gray";
  return "teal";
}

function runStatusLabel(status: ProductRun["status"]) {
  return status.replace(/_/g, " ");
}

function hasResultBundle(run: ProductRun) {
  return run.status === "succeeded" || run.status === "partially_succeeded";
}

function ResultBundlesSummary({ artifacts }: { artifacts: ProductRunArtifact[] }) {
  return (
    <Card>
      <CardHeader title="Result bundles" eyebrow="Product-safe artifacts" action={<StatusBadge tone="teal">{artifacts.length}</StatusBadge>} />
      <CardBody>
        {artifacts.length > 0 ? (
          <ul className="grid gap-3 text-sm">
            {artifacts.map((artifact) => (
              <li key={artifact.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <a className="focus-ring font-semibold text-ink-950" href={`/projects/${artifact.project_id}/runs/${artifact.run_id}/result`}>
                  {artifactDisplayName(artifact)}
                </a>
                <p className="mt-1 text-ink-500">{dateLabel(artifact.created_at)}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm leading-6 text-ink-600">
            Result bundles will appear after a bounded discovery workflow completes. V0.3 does not expose raw internal
            execution details.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function artifactDisplayName(artifact: ProductRunArtifact) {
  const metadata =
    artifact.metadata && typeof artifact.metadata === "object" && !Array.isArray(artifact.metadata)
      ? (artifact.metadata as Record<string, unknown>)
      : {};

  return typeof metadata.display_name === "string" ? metadata.display_name : "Product-safe result bundle";
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

  const { data: runData } = await supabase
    .from("product_runs")
    .select("id, organization_id, project_id, created_by_user_id, run_type, mode, status, disease_or_goal, target_focus, options, progress, result_summary, error_summary, started_at, completed_at, created_at, updated_at")
    .eq("organization_id", membership.organization_id)
    .eq("project_id", projectId)
    .order("created_at", { ascending: false })
    .limit(5);
  const runs = (runData ?? []) as ProductRun[];

  const { data: artifactData } = await supabase
    .from("product_run_artifacts")
    .select("id, organization_id, project_id, run_id, artifact_type, storage_kind, storage_path, content_json, content_text, sha256, size_bytes, public_to_user, admin_only, created_at, metadata")
    .eq("organization_id", membership.organization_id)
    .eq("project_id", projectId)
    .eq("artifact_type", "result_bundle_json")
    .order("created_at", { ascending: false })
    .limit(5);
  const artifacts = (artifactData ?? []) as ProductRunArtifact[];

  return (
    <AppShell userRole={membership.role as ProductRole}>
      <PageHeader
        title={project.name}
        description={project.research_goal ?? "Project details are scoped to your active organization membership."}
        actions={
          <div className="flex flex-wrap gap-3">
            <Button href="/dashboard" variant="secondary" icon={ArrowLeft}>
              Back to dashboard
            </Button>
            <Button href={`/projects/${project.id}/runs/new`} icon={FlaskConical}>
              Start discovery run
            </Button>
          </div>
        }
      />
      <div className="space-y-6">
        <ProjectSummary project={project} />
        <ProjectUsageSummary runs={runs} artifacts={artifacts} />
        <RecentRuns projectId={project.id} runs={runs} />
        <ResultBundlesSummary artifacts={artifacts} />
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
