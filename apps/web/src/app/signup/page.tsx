import Link from "next/link";

import { SignupForm } from "@/components/auth/signup-form";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Logo } from "@/components/layout/logo";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export default function SignupPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-xl">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Create pilot account" eyebrow="Supabase Auth" />
          <CardBody>
            <SignupForm />
            <div className="mt-5">
              <ResearchUseBanner compact />
            </div>
            <p className="mt-4 text-sm leading-6 text-ink-600">
              Do not enter patient-specific or protected health information. Do not request treatment, dosing,
              synthesis, or lab protocols.
            </p>
            <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-sm font-medium text-teal-700">
              <Link href="/terms-placeholder" className="focus-ring rounded-product hover:text-teal-550">
                Terms placeholder
              </Link>
              <Link href="/privacy-placeholder" className="focus-ring rounded-product hover:text-teal-550">
                Privacy placeholder
              </Link>
            </div>
          </CardBody>
        </Card>
      </div>
    </main>
  );
}
