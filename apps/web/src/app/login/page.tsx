import Link from "next/link";

import { LoginForm } from "@/components/auth/login-form";
import { ResearchUseBanner } from "@/components/disclaimers/research-use-banner";
import { Logo } from "@/components/layout/logo";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

type LoginPageProps = {
  searchParams?: Promise<{
    error?: string;
    next?: string;
    signedOut?: string;
    passwordUpdated?: string;
  }>;
};

function safeNextPath(value?: string) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("://")) return "/dashboard";
  return value;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const next = safeNextPath(params?.next);
  const statusMessage = params?.signedOut
    ? "You have been signed out."
    : params?.passwordUpdated
      ? "Password updated. Log in with your new password."
      : params?.error
        ? "Authentication could not be completed. Try signing in again."
        : undefined;

  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Log in" eyebrow="Pilot access" />
          <CardBody>
            {statusMessage ? (
              <p className="mb-4 rounded-product border border-slatewash-200 bg-white p-3 text-sm leading-6 text-ink-700">
                {statusMessage}
              </p>
            ) : null}
            <LoginForm next={next} />
            <div className="mt-5">
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
