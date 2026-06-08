import Link from "next/link";

import { ResetPasswordForm } from "@/components/auth/reset-password-form";
import { Logo } from "@/components/layout/logo";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

export default function ResetPasswordPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slatewash-50 px-4 py-10">
      <div className="w-full max-w-lg">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <Card>
          <CardHeader title="Set new password" eyebrow="Account recovery" />
          <CardBody>
            <p className="mb-4 text-sm leading-6 text-ink-600">
              Use the latest password reset link from your email, then choose a new password.
            </p>
            <ResetPasswordForm />
            <div className="mt-4 text-sm font-medium text-teal-700">
              <Link href="/login" className="focus-ring rounded-product hover:text-teal-550">
                Back to login
              </Link>
            </div>
          </CardBody>
        </Card>
      </div>
    </main>
  );
}
