import { EvidenceExplorer } from "@/components/evidence/evidence-explorer";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { candidates, evidenceItems, projects, runs } from "@/lib/mock-data";

type EvidencePageProps = {
  params: Promise<{ projectId: string; runId: string }>;
};

export default async function EvidencePage({ params }: EvidencePageProps) {
  const { projectId, runId } = await params;
  const project = projects.find((item) => item.id === projectId);
  const run = runs.find((item) => item.id === runId && item.projectId === projectId);

  if (!project || !run) {
    return (
      <AppShell>
        <PageHeader
          title="Evidence not found"
          description="No synthetic evidence view matches this route. No backend lookup was made."
          actions={<Button href={`/projects/${projectId}/runs/${runId}`}>Back to run</Button>}
        />
        <Card>
          <CardHeader title="Unable to show evidence" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a synthetic UI demo run to review source provenance, evidence limitations, and candidate links.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Evidence"
        description="Review source provenance, independently verify evidence, and inspect synthetic evidence coverage."
        actions={<Button href={`/projects/${projectId}/runs/${runId}/result`}>Back to result bundle</Button>}
      />
      <EvidenceExplorer evidenceItems={evidenceItems} candidates={candidates} projectId={projectId} runId={runId} />
    </AppShell>
  );
}
