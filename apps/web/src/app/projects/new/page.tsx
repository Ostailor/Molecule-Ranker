import { ShieldAlert } from "lucide-react";

import { ForbiddenPage } from "@/components/auth/forbidden-page";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { CreateProjectForm } from "@/components/projects/create-project-form";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";
import { canCreateProject } from "@/lib/product/permissions";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, ProductRole } from "@/lib/supabase/types";

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

function SetupIssuePage() {
  return (
    <AppShell>
      <PageHeader title="Create project" description="Finish workspace setup before creating projects." />
      <Card>
        <CardBody className="grid gap-5 p-6 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <h2 className="text-lg font-semibold text-ink-950">Workspace membership required</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              Project creation requires an active organization membership. Finish onboarding or contact a workspace owner.
            </p>
          </div>
          <Button href="/onboarding">Finish onboarding</Button>
        </CardBody>
      </Card>
    </AppShell>
  );
}

export default async function NewProjectPage() {
  const user = await requireUser("/login?next=/projects/new");
  const supabase = await createClient();
  const { data } = await supabase
    .from("product_memberships")
    .select("id, organization_id, user_id, role, status, created_at, updated_at")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  const membership = data as Membership | null;

  if (!membership) return <SetupIssuePage />;

  const role = membership.role as ProductRole;

  if (!canCreateProject(role)) {
    return <ForbiddenPage />;
  }

  return (
    <AppShell userRole={role}>
      <PageHeader
        title="Create project"
        description="Create a tenant-scoped research planning project for your active organization."
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_0.72fr]">
        <Card>
          <CardHeader title="Project details" eyebrow="Product data" action={<StatusBadge tone="teal">{role}</StatusBadge>} />
          <CardBody>
            <CreateProjectForm />
          </CardBody>
        </Card>
        <div className="space-y-6">
          <UnsafeRequestWarning />
          <ResearchUseBanner />
          <Card>
            <CardHeader title="Project creation" eyebrow="Release V0.2" />
            <CardBody>
              <p className="text-sm leading-6 text-ink-600">
                This form writes to product_projects using your active organization membership. Viewers can read projects
                but cannot create them.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
