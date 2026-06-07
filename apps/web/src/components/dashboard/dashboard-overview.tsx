import {
  ArrowRight,
  FileArchive,
  FlaskConical,
  MessageSquareText,
  Plus,
  ShieldAlert,
  Star,
  UsersRound,
} from "lucide-react";
import Link from "next/link";
import { candidates, organization, pilotUser, projects, resultBundles, runs, usageSummary } from "@/lib/mock-data";
import { quickLinks } from "@/lib/routes";
import { compactNumber, dateLabel, percent } from "@/lib/formatting";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";

export type DashboardViewState = "normal" | "empty" | "loading" | "error";

type DashboardOverviewProps = {
  state?: DashboardViewState;
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

function DashboardErrorCard() {
  return (
    <div className="space-y-6">
      <Card>
        <CardBody className="flex flex-col gap-4 p-6 sm:flex-row sm:items-start">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-rose-50 text-rose-700">
            <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="max-w-2xl">
            <h2 className="text-lg font-semibold text-ink-950">Dashboard data unavailable</h2>
            <p className="mt-2 text-sm leading-6 text-ink-600">
              The mock dashboard could not load its research hypotheses, projects, or result bundle summaries. No user
              data was submitted.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button href="/dashboard" variant="secondary">
                Reload dashboard
              </Button>
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

function EmptyProjectsState() {
  return (
    <div className="space-y-6" aria-label="Empty projects dashboard state">
      <WelcomeCard projectCount={0} runCount={0} />
      <DashboardActions />
      <Card>
        <CardHeader title="Projects" eyebrow="Empty state" />
        <CardBody className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <h2 className="text-lg font-semibold text-ink-950">No projects yet</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              Create a project to organize research hypotheses, candidate prioritization, evidence, and result bundle
              review notes.
            </p>
          </div>
          <Button href={quickLinks.newProject} icon={Plus}>
            Create project
          </Button>
        </CardBody>
      </Card>
      <UsageSummary />
      <SavedCandidatesPlaceholder />
      <ResearchReminder />
      <FeedbackCta />
    </div>
  );
}

function WelcomeCard({ projectCount, runCount }: { projectCount: number; runCount: number }) {
  return (
    <Card>
      <CardBody className="grid gap-6 p-5 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">{organization.plan}</p>
          <h2 className="mt-2 text-2xl font-semibold text-ink-950">Welcome, {pilotUser.name}</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-600">
            Review research hypotheses, candidate prioritization, and result bundle readiness for{" "}
            {organization.name}. All mock items require expert review before any use outside planning.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:w-72">
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Projects</p>
            <p className="mt-2 text-2xl font-semibold text-ink-950">{projectCount}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Runs</p>
            <p className="mt-2 text-2xl font-semibold text-ink-950">{runCount}</p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function DashboardActions() {
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
      <Link
        href={quickLinks.newRun}
        className="focus-ring group rounded-product border border-lime-450/30 bg-lime-350/20 p-4 transition hover:bg-lime-350/30"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-ink-950">Start discovery run</p>
            <p className="mt-1 text-sm leading-6 text-ink-600">
              Generate a mock result bundle that requires expert review before advancement.
            </p>
          </div>
          <ArrowRight className="h-5 w-5 shrink-0 text-lime-900 transition group-hover:translate-x-0.5" aria-hidden="true" />
        </div>
      </Link>
    </div>
  );
}

function UsageSummary() {
  const remainingRuns = usageSummary.monthlyRunLimit - usageSummary.discoveryRunsThisMonth;

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Metric label="Discovery runs" value={String(usageSummary.discoveryRunsThisMonth)} detail="This month" icon={FlaskConical} />
      <Metric label="Evidence items" value={compactNumber(usageSummary.evidenceItemsReviewed)} detail="Synthetic rows reviewed" icon={FileArchive} />
      <Metric label="Research hypotheses" value={String(usageSummary.generatedHypotheses)} detail="Generated placeholders" icon={Star} />
      <Metric label="Run capacity" value={String(remainingRuns)} detail="Pilot preview remaining" icon={UsersRound} />
    </div>
  );
}

function ProjectsList() {
  return (
    <Card>
      <CardHeader title="Projects" eyebrow="Mock workspace" action={<StatusBadge tone="teal">{projects.length} active</StatusBadge>} />
      <CardBody>
        <DataTable
          columns={["Project", "Area", "Status", "Runs", "Updated"]}
          rows={projects.map((project) => [
            <Link key={project.id} href={`/projects/${project.id}`} className="font-semibold text-ink-950 hover:text-teal-700">
              {project.name}
            </Link>,
            project.therapeuticArea,
            <StatusBadge key={`${project.id}-status`} tone={project.status === "Active" ? "teal" : project.status === "Review" ? "amber" : "gray"}>
              {project.status}
            </StatusBadge>,
            project.runCount,
            dateLabel(project.updatedAt),
          ])}
        />
      </CardBody>
    </Card>
  );
}

function RecentDiscoveryRuns() {
  return (
    <Card>
      <CardHeader title="Recent discovery runs" eyebrow="Requires expert review" />
      <CardBody>
        <DataTable
          columns={["Run", "Status", "Candidates", "Result bundle"]}
          rows={runs.map((run) => {
            const bundle = resultBundles.find((item) => item.runId === run.id);

            return [
              <Link key={run.id} href={`/projects/${run.projectId}/runs/${run.id}`} className="font-semibold text-ink-950 hover:text-teal-700">
                {run.name}
              </Link>,
              <StatusBadge key={`${run.id}-status`} tone={run.status === "Complete" ? "green" : run.status === "Running" ? "teal" : "amber"}>
                {run.status}
              </StatusBadge>,
              run.candidateCount,
              bundle ? (
                <Link key={`${run.id}-bundle`} href={`/projects/${run.projectId}/runs/${run.id}/result`} className="font-semibold text-teal-700 hover:text-teal-550">
                  {bundle.status}
                </Link>
              ) : (
                "Not generated"
              ),
            ];
          })}
        />
      </CardBody>
    </Card>
  );
}

function SavedCandidatesPlaceholder() {
  return (
    <Card>
      <CardHeader title="Saved candidates" eyebrow="Placeholder" action={<StatusBadge tone="amber">Release V0.2</StatusBadge>} />
      <CardBody className="space-y-4">
        <p className="text-sm leading-6 text-ink-600">
          Saved candidates will collect reviewer selections from candidate prioritization. For V0.1, this card previews
          the destination without storing user selections.
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

export function DashboardOverview({ state = "normal" }: DashboardOverviewProps) {
  if (state === "loading") return <DashboardLoadingSkeleton />;
  if (state === "error") return <DashboardErrorCard />;
  if (state === "empty") return <EmptyProjectsState />;

  return (
    <div className="space-y-6">
      <WelcomeCard projectCount={projects.length} runCount={runs.length} />
      <DashboardActions />
      <UsageSummary />
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <ProjectsList />
        <RecentDiscoveryRuns />
      </div>
      <SavedCandidatesPlaceholder />
      <ResearchReminder />
      <FeedbackCta />
    </div>
  );
}
