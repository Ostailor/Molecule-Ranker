import Link from "next/link";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";
import { Logo } from "@/components/layout/logo";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export default function ForgotPasswordPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Reset password" eyebrow="Account recovery" />
          <CardBody>
            <p className="mb-4 text-sm leading-6 text-ink-600">
              Enter the email used for your MolCreate pilot account. The reset link returns here through Supabase Auth.
            </p>
            <ForgotPasswordForm />
            <div className="mt-4 text-sm font-medium text-teal-700">
              <Link href="/signup" className="focus-ring rounded-product hover:text-teal-550">
                Create a pilot account
              </Link>
            </div>
          </CardBody>
        </Card>
      </div>
    </main>
  );
}
