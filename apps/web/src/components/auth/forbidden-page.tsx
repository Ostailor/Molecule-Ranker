import { ShieldAlert } from "lucide-react";

import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export function ForbiddenPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-xl">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Access unavailable" eyebrow="403" />
          <CardBody>
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-product bg-rose-50 text-rose-700">
                <ShieldAlert className="h-5 w-5" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-semibold text-ink-950">This area is limited to workspace owners and admins.</p>
                <p className="mt-2 text-sm leading-6 text-ink-700">
                  Use the dashboard or account page for normal pilot access. No admin data is shown on this page.
                </p>
              </div>
            </div>
            <div className="mt-5 flex flex-wrap gap-3">
              <Button href="/dashboard">Go to dashboard</Button>
              <Button href="/account" variant="secondary">
                Account
              </Button>
            </div>
            <div className="mt-5">
              <ResearchUseBanner compact />
            </div>
          </CardBody>
        </Card>
      </div>
    </main>
  );
}
