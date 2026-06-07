import { CandidateDetailView } from "@/components/candidates/candidate-detail-view";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { candidates, evidenceItems, projects, runs } from "@/lib/mock-data";

type CandidateDetailPageProps = {
  params: Promise<{ projectId: string; runId: string; candidateId: string }>;
};

export default async function CandidateDetailPage({ params }: CandidateDetailPageProps) {
  const { projectId, runId, candidateId } = await params;
  const project = projects.find((item) => item.id === projectId);
  const run = runs.find((item) => item.id === runId && item.projectId === projectId);
  const candidate = candidates.find((item) => item.id === candidateId);
  const candidateEvidence = evidenceItems.filter((item) => item.candidateId === candidateId);

  if (!project || !run || !candidate) {
    return (
      <AppShell>
        <PageHeader
          title="Candidate not found"
          description="No synthetic candidate matches this route. No backend lookup was made."
          actions={<Button href={`/projects/${projectId}/runs/${runId}/candidates`}>Back to candidates</Button>}
        />
        <Card>
          <CardHeader title="Unable to show candidate detail" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Select a synthetic UI demo candidate to review score summary, evidence preview, provenance, and notes.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title={candidate.name}
        description="Review prioritization score, evidence preview, provenance, warnings/limitations, and research notes."
        actions={<Button href={`/projects/${projectId}/runs/${runId}/candidates`}>Back to candidates</Button>}
      />
      <CandidateDetailView
        candidate={candidate}
        evidence={candidateEvidence}
        projectName={project.name}
        runName={run.name}
      />
    </AppShell>
  );
}
