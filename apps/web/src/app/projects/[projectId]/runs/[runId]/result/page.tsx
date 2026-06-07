import { ResultBundleOverview } from "@/components/runs/result-bundle-overview";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { projects, resultBundles, runs } from "@/lib/mock-data";

type ResultPageProps = {
  params: Promise<{ projectId: string; runId: string }>;
};

export default async function ResultPage({ params }: ResultPageProps) {
  const { projectId, runId } = await params;
  const project = projects.find((item) => item.id === projectId);
  const run = runs.find((item) => item.id === runId && item.projectId === projectId);
  const bundle = resultBundles.find((item) => item.runId === runId);

  if (!project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Result bundle not found"
          description="No synthetic result bundle matches this route. No backend lookup was made."
          actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run</Button>}
        />
        <Card>
          <CardHeader title="Unable to show result bundle" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a synthetic UI demo run to inspect result summary, evidence coverage, limitations, and review
              checklist.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Result bundle"
        description="Review candidate ranking, evidence coverage, generated hypotheses, limitations, guardrail notices, and export readiness."
        actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run status</Button>}
      />
      <ResultBundleOverview project={project} run={run} bundle={bundle} projectId={projectId} runId={runId} />
    </AppShell>
  );
}
