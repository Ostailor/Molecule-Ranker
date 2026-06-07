import Link from "next/link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Logo } from "@/components/layout/logo";

export default function TermsPlaceholderPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-2xl">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Terms placeholder" eyebrow="Placeholder until Release V0.2" />
          <CardBody>
            {/* PLACEHOLDER_V0_1_TERMS: replace with approved legal terms for Release V0.2. */}
            <p className="text-sm leading-6 text-ink-600">
              This placeholder exists so pilot reviewers can navigate the mock login flow without broken links. It is
              not a legal terms document.
            </p>
            <div className="mt-4">
              <ResearchUseBanner compact />
            </div>
            <Link href="/login" className="focus-ring mt-4 inline-flex rounded-product text-sm font-semibold text-teal-700">
              Back to login
            </Link>
          </CardBody>
        </Card>
      </div>
    </main>
  );
}
