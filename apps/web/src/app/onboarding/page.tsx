import { CheckCircle2, UserRound, Building2, ShieldCheck } from "lucide-react";

import { OnboardingForm } from "@/components/onboarding/onboarding-form";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";
import { requireUser } from "@/lib/supabase/auth";
import { createClient } from "@/lib/supabase/server";
import type { Membership, Organization, Profile } from "@/lib/supabase/types";

function stringMetadataValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

export default async function OnboardingPage() {
  const user = await requireUser("/login?next=/onboarding");
  const supabase = await createClient();
  const profilePromise = supabase.from("product_profiles").select("*").eq("id", user.id).maybeSingle();
  const membershipPromise = supabase
    .from("product_memberships")
    .select("organization_id, role, status")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  const [profileResult, membershipResult] = await Promise.all([profilePromise, membershipPromise]);
  const profile = profileResult.data as Profile | null;
  const membership = membershipResult.data as Pick<Membership, "organization_id" | "role" | "status"> | null;
  const { data: organization } = membership?.organization_id
    ? await supabase.from("product_organizations").select("id, name, slug, status").eq("id", membership.organization_id).maybeSingle()
    : { data: null };
  const currentOrganization = organization as Pick<Organization, "id" | "name" | "slug" | "status"> | null;
  const metadataDisplayName = stringMetadataValue(user.user_metadata?.display_name);
  const defaultDisplayName = profile?.display_name ?? metadataDisplayName ?? user.email ?? "";
  const researchUseAcknowledged = Boolean(profile?.research_use_acknowledged_at);
  const onboardingComplete = Boolean(profile?.onboarding_completed);

  return (
    <AppShell userRole={membership?.role}>
      <PageHeader
        title="Onboarding"
        description="Confirm the pilot workspace boundary, profile, and organization before using the product dashboard."
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_0.9fr]">
        <Card>
          <CardHeader title="Finish onboarding" eyebrow="Release V0.2" />
          <CardBody>
            <OnboardingForm defaultDisplayName={defaultDisplayName} organizationName={currentOrganization?.name ?? undefined} />
          </CardBody>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Current account status" eyebrow="Authenticated user" />
            <CardBody>
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                  <div className="flex items-start gap-3">
                    <UserRound className="mt-0.5 h-4 w-4 text-teal-700" aria-hidden="true" />
                    <div>
                      <p className="text-sm font-semibold text-ink-950">Profile</p>
                      <p className="mt-1 text-sm leading-6 text-ink-600">{defaultDisplayName || user.email}</p>
                    </div>
                  </div>
                  <StatusBadge tone={profile ? "green" : "amber"}>{profile ? "Found" : "Missing"}</StatusBadge>
                </div>

                <div className="flex items-start justify-between gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                  <div className="flex items-start gap-3">
                    <Building2 className="mt-0.5 h-4 w-4 text-teal-700" aria-hidden="true" />
                    <div>
                      <p className="text-sm font-semibold text-ink-950">Organization</p>
                      <p className="mt-1 text-sm leading-6 text-ink-600">{currentOrganization?.name ?? "Create one to continue"}</p>
                    </div>
                  </div>
                  <StatusBadge tone={currentOrganization ? "green" : "amber"}>{currentOrganization ? "Found" : "Needed"}</StatusBadge>
                </div>

                <div className="flex items-start justify-between gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                  <div className="flex items-start gap-3">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 text-teal-700" aria-hidden="true" />
                    <div>
                      <p className="text-sm font-semibold text-ink-950">Membership</p>
                      <p className="mt-1 text-sm leading-6 text-ink-600">
                        {membership ? `${membership.role} membership is active` : "An owner membership will be created if needed"}
                      </p>
                    </div>
                  </div>
                  <StatusBadge tone={membership ? "green" : "amber"}>{membership ? "Active" : "Needed"}</StatusBadge>
                </div>

                <div className="flex items-start justify-between gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                  <div className="flex items-start gap-3">
                    <ShieldCheck className="mt-0.5 h-4 w-4 text-teal-700" aria-hidden="true" />
                    <div>
                      <p className="text-sm font-semibold text-ink-950">Research-use boundary</p>
                      <p className="mt-1 text-sm leading-6 text-ink-600">
                        {researchUseAcknowledged ? "Previously acknowledged" : "Acknowledgement required before continuing"}
                      </p>
                    </div>
                  </div>
                  <StatusBadge tone={researchUseAcknowledged ? "green" : "amber"}>
                    {researchUseAcknowledged ? "Acknowledged" : "Required"}
                  </StatusBadge>
                </div>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Safety boundary" eyebrow={onboardingComplete ? "Complete" : "Required"} />
            <CardBody>
              <p className="text-sm leading-6 text-ink-700">
                MolCreate is limited to research-planning artifacts and hypotheses. Do not enter patient-specific data,
                protected health information, payment information, treatment requests, synthesis plans, or wet-lab execution instructions.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
