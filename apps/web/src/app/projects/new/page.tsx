import { ArrowRight, ShieldAlert } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";

function UnsafeRequestWarning() {
  return (
    <Card>
      <CardBody className="flex gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" aria-hidden="true" />
        <div>
          <h2 className="text-sm font-semibold text-ink-950">Before creating a project</h2>
          <div className="mt-2 space-y-1 text-sm leading-6 text-ink-700">
            <p>Do not enter patient-specific or protected health information.</p>
            <p>Do not request treatment, dosing, synthesis, or lab protocols.</p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

export default function NewProjectPage() {
  return (
    <AppShell>
      <PageHeader
        title="Create project"
        description="Set up a synthetic research-planning workspace. This V0.1 form does not submit data or call a backend."
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_0.72fr]">
        <Card>
          <CardHeader title="Project details" eyebrow="Mock form" />
          <CardBody>
            <form className="grid gap-4" aria-label="Mock create project form">
              <label>
                <span className="text-sm font-semibold text-ink-800">Project name</span>
                <input
                  className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                  defaultValue="ExampleDiseaseA hypothesis review"
                  name="project-name"
                />
              </label>
              <label>
                <span className="text-sm font-semibold text-ink-800">Research goal</span>
                <textarea
                  className="mt-2 min-h-28 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                  defaultValue="Prioritize synthetic research hypotheses for expert review and result bundle planning."
                  name="research-goal"
                />
              </label>
              <div className="grid gap-4 md:grid-cols-2">
                <label>
                  <span className="text-sm font-semibold text-ink-800">Disease or area</span>
                  <input
                    className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                    defaultValue="ExampleDiseaseA"
                    name="disease-or-area"
                  />
                </label>
                <label>
                  <span className="text-sm font-semibold text-ink-800">Optional target focus</span>
                  <input
                    className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                    defaultValue="ExampleTargetA"
                    name="target-focus"
                  />
                </label>
              </div>
              <label>
                <span className="text-sm font-semibold text-ink-800">Notes</span>
                <textarea
                  className="mt-2 min-h-24 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                  defaultValue="Use demo-only evidence rows and flag all outputs as requiring expert review."
                  name="notes"
                />
              </label>
              <div className="flex flex-wrap gap-3 pt-2">
                <Button href="/dashboard?projectCreated=1" icon={ArrowRight}>
                  Create mock project
                </Button>
                <Button href="/dashboard" variant="secondary">
                  Cancel
                </Button>
              </div>
            </form>
          </CardBody>
        </Card>
        <div className="space-y-6">
          <UnsafeRequestWarning />
          <ResearchUseBanner />
          <Card>
            <CardHeader title="Mock behavior" eyebrow="Release V0.1" />
            <CardBody>
              <p className="text-sm leading-6 text-ink-600">
                The create action routes to the dashboard with a query parameter. No project record is saved and no
                backend request is made.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}

