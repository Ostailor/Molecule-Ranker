import { ArrowRight, FileArchive, FlaskConical, Plus, Star } from "lucide-react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import { candidates, projects, resultBundles, runs, usageSummary } from "@/lib/mock-data";
import { dateLabel, percent } from "@/lib/formatting";
import { quickLinks } from "@/lib/routes";

type ProjectPageProps = {
  params: Promise<{
    projectId: string;
  }>;
  searchParams?: Promise<{
    state?: string;
  }>;
};

function ProjectNotFound({ projectId }: { projectId: string }) {
  return (
    <AppShell>
      <PageHeader title="Project not found" description="This mock project does not exist in the synthetic demo dataset." />
      <Card>
        <CardBody className="grid gap-5 p-6 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <h2 className="text-lg font-semibold text-ink-950">No project matches {projectId}</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              Choose a mock project from the dashboard or create a new placeholder project. No backend lookup was made.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button href="/dashboard" variant="secondary">
              Back to dashboard
            </Button>
            <Button href={quickLinks.newProject} icon={Plus}>
              Create project
            </Button>
          </div>
        </CardBody>
      </Card>
    </AppShell>
  );
}

function ProjectSummary({ project, runCount }: { project: (typeof projects)[number]; runCount: number }) {
  return (
    <Card>
      <CardHeader title="Project summary" eyebrow={project.therapeuticArea} action={<StatusBadge tone="teal">{project.status}</StatusBadge>} />
      <CardBody className="space-y-4">
        <p className="text-sm leading-6 text-ink-600">{project.objective}</p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Updated</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">{dateLabel(project.updatedAt)}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Discovery runs</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">{runCount}</p>
          </div>
          <div className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-ink-400">Data mode</p>
            <p className="mt-2 text-sm font-semibold text-ink-950">Synthetic mock data</p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function NoRunsYet({ projectId }: { projectId: string }) {
  return (
    <Card>
      <CardHeader title="Recent runs" eyebrow="No runs yet" />
      <CardBody className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
        <div>
          <h2 className="text-lg font-semibold text-ink-950">No discovery runs yet</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
            Start a discovery run to create a mock result bundle for candidate prioritization and expert review.
          </p>
        </div>
        <Button href={`/projects/${projectId}/runs/new`} icon={Plus}>
          Start new discovery run
        </Button>
      </CardBody>
    </Card>
  );
}

function RecentRuns({ projectRuns }: { projectRuns: typeof runs }) {
  return (
    <Card>
      <CardHeader title="Recent runs" eyebrow="Project activity" />
      <CardBody>
        <DataTable
          columns={["Run", "Status", "Candidates", "Started"]}
          rows={projectRuns.map((run) => [
            <Link key={run.id} href={`/projects/${run.projectId}/runs/${run.id}`} className="font-semibold text-ink-950 hover:text-teal-700">
              {run.name}
            </Link>,
            <StatusBadge key={`${run.id}-status`} tone={run.status === "Complete" ? "green" : run.status === "Running" ? "teal" : "amber"}>
              {run.status}
            </StatusBadge>,
            run.candidateCount,
            dateLabel(run.startedAt),
          ])}
        />
      </CardBody>
    </Card>
  );
}

function SavedCandidates() {
  return (
    <Card>
      <CardHeader title="Saved candidates" eyebrow="Mock preview" />
      <CardBody>
        <div className="grid gap-3 sm:grid-cols-2">
          {candidates.slice(0, 3).map((candidate) => (
            <div key={candidate.id} className="rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-ink-950">{candidate.name}</p>
                <StatusBadge tone={candidate.confidence === "Medium" ? "amber" : "gray"}>{candidate.confidence}</StatusBadge>
              </div>
              <p className="mt-2 text-xs leading-5 text-ink-600">
                {percent(candidate.score)} score for candidate prioritization. Requires expert review.
              </p>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function ProjectUsage({ projectRuns }: { projectRuns: typeof runs }) {
  const candidateTotal = projectRuns.reduce((total, run) => total + run.candidateCount, 0);
  const evidenceTotal = projectRuns.reduce((total, run) => total + run.evidenceCount, 0);

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Metric label="Project runs" value={String(projectRuns.length)} detail="Synthetic discovery runs" icon={FlaskConical} />
      <Metric label="Candidate rows" value={String(candidateTotal)} detail="Prioritization placeholders" icon={Star} />
      <Metric label="Evidence rows" value={String(evidenceTotal || usageSummary.evidenceItemsReviewed)} detail="Demo review items" icon={FileArchive} />
    </div>
  );
}

function ResultBundlesList({ projectRuns }: { projectRuns: typeof runs }) {
  const runIds = new Set(projectRuns.map((run) => run.id));
  const bundles = resultBundles.filter((bundle) => runIds.has(bundle.runId));

  return (
    <Card>
      <CardHeader title="Result bundles" eyebrow="Review packets" />
      <CardBody>
        {bundles.length > 0 ? (
          <DataTable
            columns={["Bundle", "Status", "Sections", "Exported"]}
            rows={bundles.map((bundle) => [
              bundle.name,
              <StatusBadge key={`${bundle.id}-status`} tone={bundle.status === "Ready for review" ? "green" : "amber"}>
                {bundle.status}
              </StatusBadge>,
              bundle.sections.length,
              bundle.exportedAt ? dateLabel(bundle.exportedAt) : "Not exported",
            ])}
          />
        ) : (
          <p className="text-sm leading-6 text-ink-600">
            No result bundles are linked yet. Start a discovery run to create a mock review packet.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

export default async function ProjectPage({ params, searchParams }: ProjectPageProps) {
  const { projectId } = await params;
  const query = await searchParams;
  const project = projects.find((item) => item.id === projectId);

  if (!project) return <ProjectNotFound projectId={projectId} />;

  const projectRuns = query?.state === "no-runs" ? [] : runs.filter((run) => run.projectId === project.id);

  return (
    <AppShell>
      <PageHeader
        title={project.name}
        description={project.objective}
        actions={
          <Button href={`/projects/${project.id}/runs/new`} icon={Plus}>
            Start new discovery run
          </Button>
        }
      />
      <div className="space-y-6">
        <ProjectSummary project={project} runCount={projectRuns.length} />
        <ProjectUsage projectRuns={projectRuns} />
        {projectRuns.length > 0 ? <RecentRuns projectRuns={projectRuns} /> : <NoRunsYet projectId={project.id} />}
        <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
          <SavedCandidates />
          <ResultBundlesList projectRuns={projectRuns} />
        </div>
        <Card>
          <CardBody className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-base font-semibold text-ink-950">Ready to continue?</h2>
              <p className="mt-1 text-sm leading-6 text-ink-600">
                Start a discovery run to generate a mock result bundle that requires expert review.
              </p>
            </div>
            <Button href={`/projects/${project.id}/runs/new`} icon={ArrowRight}>
              Start new discovery run
            </Button>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}

