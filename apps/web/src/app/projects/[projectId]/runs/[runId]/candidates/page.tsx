import { CandidateExplorer } from "@/components/candidates/candidate-explorer";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { candidates, projects, runs } from "@/lib/mock-data";

type CandidatesPageProps = {
  params: Promise<{ projectId: string; runId: string }>;
};

export default async function CandidatesPage({ params }: CandidatesPageProps) {
  const { projectId, runId } = await params;
  const project = projects.find((item) => item.id === projectId);
  const run = runs.find((item) => item.id === runId && item.projectId === projectId);

  if (!project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Candidates not found"
          description="No synthetic candidate ranking matches this route. No backend lookup was made."
          actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run</Button>}
        />
        <Card>
          <CardHeader title="Unable to show candidate ranking" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a synthetic UI demo run to review ranked candidates, filters, and saved status placeholders.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Candidate ranking"
        description="Filter, sort, save, and open synthetic candidate hypotheses for research review."
        actions={<Button href={`/projects/${projectId}/runs/${runId}/result`}>Back to result bundle</Button>}
      />
      <CandidateExplorer candidates={candidates} projectId={projectId} runId={runId} />
    </AppShell>
  );
}
