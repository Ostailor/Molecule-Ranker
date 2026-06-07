import { AlertTriangle, BarChart3, FlaskConical, FolderKanban, LifeBuoy, UsersRound } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import { featureFlags } from "@/lib/feature-flags";
import { adminSummary, feedbackMessages, pilotUser, projects, runs } from "@/lib/mock-data";

export default function AdminPage() {
  const openFeedback = feedbackMessages.filter((message) => message.status === "Open").length;

  return (
    <AppShell>
      <PageHeader
        title="Admin"
        description="Admin-only placeholder for pilot workspace review. This area is not available to normal users."
      />

      <Card className="mb-6 border-amber-200 bg-amber-50">
        <CardBody>
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" aria-hidden="true" />
            <p className="text-sm leading-6 text-ink-700">
              Admin-only placeholder. This page uses synthetic UI data and is not available to normal users.
            </p>
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric label="Pilot users summary mock" value={String(adminSummary.pilotSeats)} detail="Pilot seats" icon={UsersRound} />
        <Metric label="Projects mock" value={String(projects.length)} detail="Synthetic projects" icon={FolderKanban} />
        <Metric label="Runs mock" value={String(runs.length)} detail="Synthetic discovery runs" icon={FlaskConical} />
        <Metric label="Support status mock" value={String(openFeedback)} detail="Open feedback messages" icon={LifeBuoy} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Pilot users summary mock" eyebrow="PLACEHOLDER_V0_1_ADMIN" />
          <CardBody>
            <DataTable
              columns={["User", "Role", "Email", "Status"]}
              rows={[
                [
                  pilotUser.name,
                  pilotUser.role,
                  pilotUser.email,
                  <StatusBadge key="pilot-status" tone="green">
                    Pilot
                  </StatusBadge>,
                ],
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Projects/runs mock" eyebrow="Synthetic workspace" />
          <CardBody>
            <DataTable
              columns={["Project", "Status", "Runs"]}
              rows={projects.map((project) => [
                project.name,
                <StatusBadge key={`${project.id}-status`} tone={project.status === "Active" ? "green" : project.status === "Review" ? "amber" : "gray"}>
                  {project.status}
                </StatusBadge>,
                project.runCount,
              ])}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Feature flags mock" eyebrow="Static config" />
          <CardBody>
            <DataTable
              columns={["Flag", "State", "Purpose"]}
              rows={[
                [
                  "connectedServices",
                  <StatusBadge key="services" tone="rose">
                    {String(featureFlags.connectedServices)}
                  </StatusBadge>,
                  "Keeps external connections unavailable in this mock frontend",
                ],
                [
                  "realAuth",
                  <StatusBadge key="auth" tone="rose">
                    {String(featureFlags.realAuth)}
                  </StatusBadge>,
                  "Keeps login as a mock flow",
                ],
                [
                  "generationPreview",
                  <StatusBadge key="gen" tone="green">
                    {String(featureFlags.generationPreview)}
                  </StatusBadge>,
                  "Shows generated hypothesis pages",
                ],
                [
                  "evidenceTrace",
                  <StatusBadge key="ev" tone="green">
                    {String(featureFlags.evidenceTrace)}
                  </StatusBadge>,
                  "Shows evidence review views",
                ],
                [
                  "adminConsole",
                  <StatusBadge key="admin" tone="green">
                    {String(featureFlags.adminConsole)}
                  </StatusBadge>,
                  "Shows this admin-only placeholder",
                ],
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Support status mock" eyebrow="Feedback queue" />
          <CardBody>
            <div className="grid gap-3">
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Open feedback</span>
                <StatusBadge tone="amber">{openFeedback}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <span className="text-sm font-semibold text-ink-800">Pending reviews</span>
                <StatusBadge tone="gray">{adminSummary.pendingReviews}</StatusBadge>
              </div>
              <div className="flex items-center gap-3 rounded-product border border-slatewash-200 bg-white p-3">
                <BarChart3 className="h-4 w-4 text-teal-700" aria-hidden="true" />
                <p className="text-sm leading-6 text-ink-700">{adminSummary.adminNotice}</p>
              </div>
            </div>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}
