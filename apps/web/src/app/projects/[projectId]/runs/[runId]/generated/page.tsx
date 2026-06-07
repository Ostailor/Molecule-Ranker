import { GeneratedHypothesesExplorer } from "@/components/generated/generated-hypotheses-explorer";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { featureFlags } from "@/lib/feature-flags";
import { candidates, generatedHypotheses, projects, runs } from "@/lib/mock-data";

type GeneratedPageProps = {
  params: Promise<{ projectId: string; runId: string }>;
  searchParams?: Promise<{ state?: string }>;
};

export default async function GeneratedPage({ params, searchParams }: GeneratedPageProps) {
  const { projectId, runId } = await params;
  const query = await searchParams;
  const project = projects.find((item) => item.id === projectId);
  const run = runs.find((item) => item.id === runId && item.projectId === projectId);
  const featureEnabled = featureFlags.generationPreview && query?.state !== "disabled";
  const visibleHypotheses = query?.state === "empty" ? [] : generatedHypotheses;

  if (!project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Generated hypotheses not found"
          description="No synthetic generated-hypotheses view matches this route. No backend lookup was made."
          actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run</Button>}
        />
        <Card>
          <CardHeader title="Unable to show generated hypotheses" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a synthetic UI demo run to review generated hypotheses and human-review boundaries.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Generated hypotheses"
        description="Review generated research-planning hypotheses with explicit no-direct-evidence and human-review boundaries."
        actions={<Button href={`/projects/${projectId}/runs/${runId}/result`}>Back to result bundle</Button>}
      />
      <GeneratedHypothesesExplorer
        candidates={candidates}
        featureEnabled={featureEnabled}
        hypotheses={visibleHypotheses}
      />
    </AppShell>
  );
}
