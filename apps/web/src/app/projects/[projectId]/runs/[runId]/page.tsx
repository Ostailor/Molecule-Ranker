import { RunSummary, type DiscoveryRunViewState } from "@/components/runs/run-summary";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { projects, runs } from "@/lib/mock-data";

type RunPageProps = {
  params: Promise<{ projectId: string; runId: string }>;
  searchParams?: Promise<{ state?: string }>;
};

const runStates = new Set<DiscoveryRunViewState>(["queued", "running", "completed", "failed", "partial", "cancelled"]);

export default async function RunPage({ params, searchParams }: RunPageProps) {
  const { projectId, runId } = await params;
  const query = await searchParams;
  const project = projects.find((item) => item.id === projectId);
  const run = runs.find((item) => item.id === runId && item.projectId === projectId);
  const requestedState = query?.state;
  const runState = isRunState(requestedState) ? requestedState : stateFromRunStatus(run?.status);
  const resultHref = `/projects/${projectId}/runs/${runId}/result`;

  if (!project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Discovery run not found"
          description="No synthetic run matches this route. No backend lookup was made."
          actions={<Button href={`/projects/${projectId}`}>Back to project</Button>}
        />
        <Card>
          <CardHeader title="Unable to show run status" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a synthetic UI demo project and discovery run to inspect the bounded workflow timeline.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Discovery run workspace"
        description="Inspect run status, coarse workflow steps, timestamps, reviewer warnings, and result bundle readiness."
        actions={
          runState === "completed" ? (
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

function isRunState(value: string | undefined): value is DiscoveryRunViewState {
  return Boolean(value && runStates.has(value as DiscoveryRunViewState));
}

function stateFromRunStatus(status: string | undefined): DiscoveryRunViewState {
  if (status === "Complete") return "completed";
  if (status === "Running") return "running";
  if (status === "Needs review") return "partial";
  return "queued";
}
