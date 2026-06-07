import Link from "next/link";
import { Building2, FileText, ShieldCheck, UserRound } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PageHeader } from "@/components/layout/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Metric } from "@/components/ui/metric";
import { StatusBadge } from "@/components/ui/status-badge";
import { organization, pilotUser } from "@/lib/mock-data";

export default function AccountPage() {
  return (
    <AppShell>
      <PageHeader title="Account" description="Mock user, organization, and research-use settings." />

      <div className="grid gap-4 md:grid-cols-3">
        <Metric label="Profile placeholder" value={pilotUser.name} detail={pilotUser.role} icon={UserRound} />
        <Metric label="Organization placeholder" value={organization.name} detail={organization.plan} icon={Building2} />
        <Metric label="Research-use acknowledgement" value="Required" detail="Mock account setting" icon={ShieldCheck} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="Profile placeholder" eyebrow="PLACEHOLDER_V0_1_ACCOUNT" />
          <CardBody className="space-y-3 text-sm">
            <AccountRow label="Name" value={pilotUser.name} />
            <AccountRow label="Email" value={pilotUser.email} />
            <AccountRow label="Role" value={pilotUser.role} />
            <div className="flex items-center justify-between gap-4">
              <span className="text-ink-600">Authentication</span>
              <StatusBadge tone="gray">Mock only</StatusBadge>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Organization placeholder" eyebrow="Synthetic workspace" />
          <CardBody className="space-y-3 text-sm">
            <AccountRow label="Organization" value={organization.name} />
            <AccountRow label="Plan" value={organization.plan} />
            <AccountRow label="Workspace type" value={organization.workspaceType} />
            <div className="flex items-center justify-between gap-4">
              <span className="text-ink-600">Data mode</span>
              <StatusBadge tone="amber">Synthetic UI demo</StatusBadge>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Research-use acknowledgement" eyebrow="Required boundary" />
          <CardBody>
            <label className="flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
              <input
                type="checkbox"
                defaultChecked
                className="mt-1 rounded border-amber-300 text-teal-550 focus:ring-teal-550"
              />
              <span className="text-sm leading-6 text-ink-700">
                I acknowledge MolCreate generates research-planning artifacts and hypotheses only: not medical advice,
                not clinical validation, not lab protocols, not synthesis plans, and does not provide dosing guidance.
              </span>
            </label>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Terms and privacy" eyebrow="Placeholder links" />
          <CardBody>
            <div className="grid gap-3 sm:grid-cols-2">
              <AccountLink href="/terms-placeholder" label="Terms placeholder" />
              <AccountLink href="/privacy-placeholder" label="Privacy placeholder" />
            </div>
            <p className="mt-4 text-sm leading-6 text-ink-600">
              These links are placeholders for Release V0.1 and do not represent finalized legal documents.
            </p>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  );
}

function AccountRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slatewash-200 pb-2 last:border-b-0 last:pb-0">
      <span className="text-ink-600">{label}</span>
      <span className="text-right font-semibold text-ink-950">{value}</span>
    </div>
  );
}

function AccountLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="focus-ring flex items-center gap-2 rounded-product border border-slatewash-200 bg-white px-3 py-2 text-sm font-semibold text-ink-800 transition hover:border-teal-450/40 hover:bg-teal-450/5"
    >
      <FileText className="h-4 w-4 text-teal-700" aria-hidden="true" />
      {label}
    </Link>
  );
}
