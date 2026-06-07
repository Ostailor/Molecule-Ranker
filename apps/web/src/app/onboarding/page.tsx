import { Mail, Gauge, Building2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { quickLinks } from "@/lib/routes";

const onboardingChecklist = [
  "Confirm research-use boundary",
  "Create first project",
  "Run first discovery workflow",
  "Review result bundle",
  "Export/save candidates",
];

export default function OnboardingPage() {
  return (
    <AppShell>
      <PageHeader
        title="Onboarding"
        description="Placeholder setup flow for V0.1 pilot review. This page does not store user data or call a backend."
        actions={<Button href={quickLinks.newProject}>Create first project</Button>}
      />
      {/* PLACEHOLDER_V0_1_ONBOARDING: remove this mock setup surface when Release V0.2 workspace onboarding ships. */}
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader title="Release V0.2 readiness checklist" eyebrow="Placeholder" />
          <CardBody>
            <ol className="space-y-3">
              {onboardingChecklist.map((item, index) => (
                <li key={item} className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-product bg-teal-450/10 text-xs font-semibold text-teal-700">
                    {index + 1}
                  </span>
                  <span className="text-sm font-semibold text-ink-950">{item}</span>
                </li>
              ))}
            </ol>
          </CardBody>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Mock organization" eyebrow="Placeholder card" />
            <CardBody>
              <div className="flex items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
                  <Building2 className="h-5 w-5" aria-hidden="true" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-ink-950">Demo Research Organization</p>
                  <p className="mt-1 text-sm leading-6 text-ink-600">
                    Static placeholder for pilot walkthroughs. No organization data is saved.
                  </p>
                </div>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Usage-limit preview" eyebrow="Mock limits" />
            <CardBody>
              <div className="flex items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
                  <Gauge className="h-5 w-5" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="font-semibold text-ink-950">Discovery runs</span>
                    <span className="text-ink-600">3 of 10 previewed</span>
                  </div>
                  <div className="mt-3 h-2 rounded-full bg-slatewash-100">
                    <div className="h-2 w-[30%] rounded-full bg-teal-450" />
                  </div>
                  <p className="mt-3 text-sm leading-6 text-ink-600">
                    Limits are illustrative only. No usage counter is stored in V0.1.
                  </p>
                </div>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Support contact" eyebrow="Pilot support" />
            <CardBody>
              <div className="flex items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
                  <Mail className="h-5 w-5" aria-hidden="true" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-ink-950">Questions or access changes</p>
                  <p className="mt-1 text-sm leading-6 text-ink-600">
                    Use the Feedback page for pilot notes. This placeholder does not send email or create tickets.
                  </p>
                </div>
              </div>
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
