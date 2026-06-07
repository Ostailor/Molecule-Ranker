import Link from "next/link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Logo } from "@/components/layout/logo";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Log in" eyebrow="Placeholder until Release V0.2" />
          <CardBody>
            {/* PLACEHOLDER_V0_1_AUTH: remove this entire mock form when Release V0.2 real auth is implemented. */}
            <form className="space-y-4" aria-label="Mock login placeholder">
              <label className="block">
                <span className="text-sm font-semibold text-ink-800">Email</span>
                <input
                  className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
                  aria-describedby="mock-auth-note"
                  defaultValue="research.ops@example.com"
                  type="email"
                />
              </label>
              <label className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
                <input
                  className="mt-1 rounded border-slatewash-200 text-teal-550 focus:ring-teal-550"
                  type="checkbox"
                  required
                />
                <span className="text-sm leading-6 text-ink-700">
                  I acknowledge MolCreate is for research-planning artifacts and hypotheses only.
                </span>
              </label>
              <button
                className="flex h-9 w-full cursor-not-allowed items-center justify-center rounded-product border border-slatewash-200 bg-slatewash-100 px-3 text-sm font-semibold text-ink-400"
                disabled
                type="button"
              >
                Continue
              </button>
            </form>
            <div id="mock-auth-note" className="mt-5 rounded-product border border-slatewash-200 bg-white p-3">
              <p className="text-sm font-semibold text-ink-950">Pilot access note</p>
              <p className="mt-1 text-sm leading-6 text-ink-600">
                This is a V0.1 placeholder. It does not submit credentials, store user data, or call a backend.
                Release V0.2 will replace this block with the approved auth flow.
              </p>
            </div>
            <div className="mt-4">
              <ResearchUseBanner compact />
            </div>
            <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-sm font-medium text-teal-700">
              <Link href="/#not" className="focus-ring rounded-product hover:text-teal-550">
                Research-use disclaimer
              </Link>
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
