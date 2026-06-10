import {
  Building2,
  FileArchive,
  FlaskConical,
  MessageSquareText,
  Plus,
  ShieldAlert,
  Star,
  UsersRound,
} from "lucide-react";
import Link from "next/link";
import { candidates } from "@/lib/mock-data";
import { quickLinks } from "@/lib/routes";
import { compactNumber, dateLabel, percent } from "@/lib/formatting";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import { productFeatureFlags } from "@/lib/product/feature-flags";
import type { ProductRole } from "@/lib/supabase/types";

export type DashboardViewState = "normal" | "empty" | "loading" | "error";

export type DashboardProject = {
  id: string;
  name: string;
  researchGoal: string | null;
  diseaseFocus: string | null;
  targetFocus: string | null;
  status: string;
  updatedAt: string;
};

export type DashboardRun = {
  id: string;
  projectId: string;
  projectName: string;
  diseaseOrGoal: string;
  mode: string;
  status: string;
  createdAt: string;
  completedAt: string | null;
  hasResultBundle: boolean;
};

export type DashboardUsageSummary = {
  eventsThisMonth: number;
  totalQuantityThisMonth: number;
  projectEventsThisMonth: number;
  feedbackEventsThisMonth: number;
};

type DashboardOverviewProps = {
  state?: DashboardViewState;
  displayName: string;
  organizationName: string;
  role: ProductRole;
  plan: string;
  projects: DashboardProject[];
  recentRuns: DashboardRun[];
  usage: DashboardUsageSummary;
};

function SkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-product bg-slatewash-100 ${className}`} />;
}

function DashboardLoadingSkeleton() {
  return (
    <div className="space-y-6" aria-label="Dashboard loading skeleton">
      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <SkeletonBlock className="h-52" />
        <SkeletonBlock className="h-52" />
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SkeletonBlock className="h-32" />
        <SkeletonBlock className="h-32" />
        <SkeletonBlock className="h-32" />
        <SkeletonBlock className="h-32" />
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <SkeletonBlock className="h-72" />
        <SkeletonBlock className="h-72" />
      </div>
    </div>
  );
}

export function DashboardSetupIssuePage({ email }: { email: string }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardBody className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-rose-50 text-rose-700">
            <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="max-w-2xl">
            <h2 className="text-lg font-semibold text-ink-950">Workspace setup needs attention</h2>
            <p className="mt-2 text-sm leading-6 text-ink-600">
              This signed-in account does not have an active organization membership yet. Use onboarding to finish setup
              or contact the pilot workspace owner. Signed in as {email}.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button href="/onboarding" variant="secondary">Finish onboarding</Button>
              <Button href="/feedback" icon={MessageSquareText}>
                Share feedback
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>
      <ResearchUseBanner />
    </div>
  );
}

export function DashboardAccountStatusPage({ organizationName, status }: { organizationName: string; status: string }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardBody className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-amber-50 text-amber-700">
            <Building2 className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="max-w-2xl">
            <h2 className="text-lg font-semibold text-ink-950">Account status limits dashboard access</h2>
            <p className="mt-2 text-sm leading-6 text-ink-600">
              {organizationName} is currently marked {status}. Project and usage data stay hidden until the workspace is active.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button href="/account" variant="secondary">Review account</Button>
              <Button href="/feedback" icon={MessageSquareText}>
                Share feedback
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>
      <ResearchUseBanner />
    </div>
  );
}

function EmptyProjectsState({
  displayName,
  organizationName,
  role,
  plan,
  usage,
}: Omit<DashboardOverviewProps, "projects" | "recentRuns" | "state">) {
  return (
    <div className="space-y-6" aria-label="Empty projects dashboard state">
      <WelcomeCard displayName={displayName} organizationName={organizationName} role={role} plan={plan} projectCount={0} />
      <DashboardActions projects={[]} />
      <Card>
        <CardHeader title="Projects" eyebrow="Empty state" />
        <CardBody className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <h2 className="text-lg font-semibold text-ink-950">No projects yet</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              Create a project to organize research questions, candidate prioritization, evidence, and result bundle
              review notes for this organization.
            </p>
          </div>
          <Button href={quickLinks.newProject} icon={Plus}>
            Create project
          </Button>
        </CardBody>
      </Card>
      <UsageSummary usage={usage} projectCount={0} />
      <RecentDiscoveryRuns recentRuns={[]} projects={[]} />
      <SavedCandidatesPlaceholder />
      <ResearchReminder />
      <FeedbackCta />
    </div>
  );
}

function WelcomeCard({
  displayName,
  organizationName,
  role,
  plan,
  projectCount,
}: {
  displayName: string;
  organizationName: string;
  role: ProductRole;
  plan: string;
  projectCount: number;
}) {
  return (
    <Card>
      <CardBody className="grid gap-6 p-5 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">{plan}</p>
          <h2 className="mt-2 text-2xl font-semibold text-ink-950">Welcome, {displayName}</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-600">
            Review research projects, tenant-scoped usage, and V0.3 workflow placeholders for {organizationName}.
            Your current role is {role}. All product outputs require expert review before any use outside planning.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:w-72">
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Projects</p>
            <p className="mt-2 text-2xl font-semibold text-ink-950">{projectCount}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Role</p>
            <p className="mt-2 text-2xl font-semibold text-ink-950 capitalize">{role}</p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function DashboardActions({ projects }: { projects: DashboardProject[] }) {
  const firstProject = projects[0];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Link
        href={quickLinks.newProject}
        className="focus-ring group rounded-product border border-teal-450/30 bg-teal-450/10 p-4 transition hover:bg-teal-450/15"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-ink-950">Create project</p>
            <p className="mt-1 text-sm leading-6 text-ink-600">
              Define a research question and review boundaries for candidate prioritization.
            </p>
          </div>
          <Plus className="h-5 w-5 shrink-0 text-teal-700 transition group-hover:scale-105" aria-hidden="true" />
        </div>
      </Link>
      {productFeatureFlags.discoveryRunsPlaceholder ? (
        <Link
          href={firstProject ? `/projects/${firstProject.id}/runs/new` : quickLinks.newProject}
          className="focus-ring group rounded-product border border-slatewash-200 bg-slatewash-50 p-4 transition hover:bg-white"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-ink-950">Start discovery run</p>
              <p className="mt-1 text-sm leading-6 text-ink-600">
                {firstProject
                  ? "Create a bounded mocked or dry-run discovery workflow from a project."
                  : "Create a project before starting a bounded discovery workflow."}
              </p>
            </div>
            <StatusBadge tone="teal">V0.3</StatusBadge>
          </div>
        </Link>
      ) : null}
    </div>
  );
}

function UsageSummary({ usage, projectCount }: { usage: DashboardUsageSummary; projectCount: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Metric label="Projects" value={String(projectCount)} detail="Current organization" icon={FileArchive} />
      <Metric label="Usage events" value={compactNumber(usage.eventsThisMonth)} detail="This month" icon={FlaskConical} />
      <Metric label="Usage quantity" value={compactNumber(usage.totalQuantityThisMonth)} detail="RLS-scoped sum" icon={Star} />
      <Metric label="Feedback events" value={compactNumber(usage.feedbackEventsThisMonth)} detail="This month" icon={UsersRound} />
    </div>
  );
}

function ProjectsList({ projects }: { projects: DashboardProject[] }) {
  return (
    <Card>
      <CardHeader title="Projects" eyebrow="Tenant-scoped data" action={<StatusBadge tone="teal">{projects.length} visible</StatusBadge>} />
      <CardBody>
        <DataTable
          columns={["Project", "Research goal", "Focus", "Status", "Updated"]}
          rows={projects.map((project) => [
            <Link key={project.id} href={`/projects/${project.id}`} className="font-semibold text-ink-950 hover:text-teal-700">
              {project.name}
            </Link>,
            project.researchGoal ?? "Not specified",
            project.diseaseFocus ?? project.targetFocus ?? "Not specified",
            <StatusBadge key={`${project.id}-status`} tone={project.status === "active" ? "teal" : project.status === "archived" ? "gray" : "amber"}>
              {project.status}
            </StatusBadge>,
            dateLabel(project.updatedAt),
          ])}
        />
      </CardBody>
    </Card>
  );
}

function statusTone(status: string): "green" | "teal" | "amber" | "rose" | "gray" {
  if (status === "succeeded") return "green";
  if (status === "running" || status === "queued") return "teal";
  if (status === "partially_succeeded") return "amber";
  if (status === "failed") return "rose";
  return "gray";
}

function statusLabel(status: string) {
  return status.replace(/_/g, " ");
}

function RecentDiscoveryRuns({ recentRuns, projects }: { recentRuns: DashboardRun[]; projects: DashboardProject[] }) {
  const firstProject = projects[0];

  return (
    <Card>
      <CardHeader
        title="Recent discovery runs"
        eyebrow="Tenant-scoped runs"
        action={
          <Button href={firstProject ? `/projects/${firstProject.id}/runs/new` : quickLinks.newProject} variant="secondary" icon={FlaskConical}>
            Start run
          </Button>
        }
      />
      <CardBody>
        {recentRuns.length > 0 ? (
          <DataTable
            columns={["Run", "Project", "Status", "Created", "Result"]}
            rows={recentRuns.map((run) => [
              <Link key={run.id} href={`/projects/${run.projectId}/runs/${run.id}`} className="font-semibold text-ink-950 hover:text-teal-700">
                {run.diseaseOrGoal}
              </Link>,
              run.projectName,
              <StatusBadge key={`${run.id}-status`} tone={statusTone(run.status)}>
                {statusLabel(run.status)}
              </StatusBadge>,
              dateLabel(run.createdAt),
              run.hasResultBundle ? (
                <Link key={`${run.id}-bundle`} href={`/projects/${run.projectId}/runs/${run.id}/result`} className="font-semibold text-teal-700 hover:text-teal-550">
                  Result bundle
                </Link>
              ) : (
                "Pending"
              ),
            ])}
          />
        ) : (
          <div className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <h2 className="text-lg font-semibold text-ink-950">No recent discovery runs</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
                Create a bounded mocked or dry-run discovery workflow from a project. Runs from other organizations stay hidden.
              </p>
            </div>
            <StatusBadge tone="teal">Ready</StatusBadge>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function SavedCandidatesPlaceholder() {
  return (
    <Card>
      <CardHeader title="Saved candidates" eyebrow="Placeholder until V0.3" action={<StatusBadge tone="amber">Mock only</StatusBadge>} />
      <CardBody className="space-y-4">
        <p className="text-sm leading-6 text-ink-600">
          Saved candidates will collect reviewer selections from candidate prioritization in a later release. This card
          previews the destination without storing user selections.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {candidates.slice(0, 2).map((candidate) => (
            <div key={candidate.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-ink-950">{candidate.name}</p>
                <StatusBadge tone={candidate.confidence === "Medium" ? "amber" : "gray"}>{candidate.confidence}</StatusBadge>
              </div>
              <p className="mt-2 text-xs leading-5 text-ink-600">
                {percent(candidate.score)} score. Requires expert review and supporting evidence checks.
              </p>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function ResearchReminder() {
  return (
    <Card>
      <CardHeader title="Research-use reminder" eyebrow="Boundary" />
      <CardBody>
        <ResearchUseBanner compact />
      </CardBody>
    </Card>
  );
}

function FeedbackCta() {
  return (
    <Card>
      <CardBody className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-ink-950">Help shape the dashboard</h2>
          <p className="mt-1 text-sm leading-6 text-ink-600">
            Share feedback on project review, candidate prioritization, and result bundle workflows.
          </p>
        </div>
        <Button href="/feedback" icon={MessageSquareText}>
          Share feedback
        </Button>
      </CardBody>
    </Card>
  );
}

export function DashboardOverview({
  state = "normal",
  displayName,
  organizationName,
  role,
  plan,
  projects,
  recentRuns,
  usage,
}: DashboardOverviewProps) {
  if (state === "loading") return <DashboardLoadingSkeleton />;
  if (state === "error") return <DashboardSetupIssuePage email={displayName} />;
  if (state === "empty" || projects.length === 0) {
    return (
      <EmptyProjectsState
        displayName={displayName}
        organizationName={organizationName}
        role={role}
        plan={plan}
        usage={usage}
      />
    );
  }

  return (
    <div className="space-y-6">
      <WelcomeCard displayName={displayName} organizationName={organizationName} role={role} plan={plan} projectCount={projects.length} />
      <DashboardActions projects={projects} />
      <UsageSummary usage={usage} projectCount={projects.length} />
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <ProjectsList projects={projects} />
        <RecentDiscoveryRuns recentRuns={recentRuns} projects={projects} />
      </div>
      <SavedCandidatesPlaceholder />
      <ResearchReminder />
      <FeedbackCta />
    </div>
  );
}
