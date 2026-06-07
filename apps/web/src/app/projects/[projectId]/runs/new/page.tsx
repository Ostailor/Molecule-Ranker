import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StartDiscoveryRunForm } from "@/components/runs/start-discovery-run-form";
import { projects } from "@/lib/mock-data";
import { runId } from "@/lib/routes";

type NewRunPageProps = {
  params: Promise<{ projectId: string }>;
};

export default async function NewRunPage({ params }: NewRunPageProps) {
  const { projectId } = await params;
  const project = projects.find((item) => item.id === projectId);
  const runHref = `/projects/${projectId}/runs/${runId}`;

  if (!project) {
    return (
      <AppShell>
        <PageHeader
          title="Project not found"
          description={`No synthetic project matches ${projectId}. No backend lookup was made.`}
          actions={<Button href="/dashboard">Back to dashboard</Button>}
        />
        <Card>
          <CardHeader title="Unable to start discovery run" eyebrow="Mock route state" />
          <CardBody>
            <p className="text-sm leading-6 text-ink-600">
              Create or select a synthetic UI demo project before configuring a bounded discovery workflow.
            </p>
          </CardBody>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Start discovery run"
        description="Configure a bounded discovery workflow for research hypothesis planning. This V0.1 page only creates local mock state."
      />
      <StartDiscoveryRunForm
        projectName={project.name}
        projectObjective={project.objective}
        diseaseOrArea={project.therapeuticArea}
        runHref={runHref}
      />
    </AppShell>
  );
}
