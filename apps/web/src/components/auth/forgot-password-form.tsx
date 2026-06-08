"use client";

import Link from "next/link";
import { useActionState } from "react";

import { initialAuthActionState } from "@/lib/supabase/auth-action-state";
import { forgotPasswordAction } from "@/lib/supabase/auth-actions";
import { AuthMessage } from "./auth-message";
import { AuthSubmitButton } from "./auth-submit-button";

export function ForgotPasswordForm() {
  const [state, formAction] = useActionState(forgotPasswordAction, initialAuthActionState);

  return (
    <form action={formAction} className="space-y-4" aria-label="Request password reset">
      <AuthMessage message={state.message} status={state.status} />
      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Email</span>
        <input
          autoComplete="email"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          name="email"
          required
          type="email"
        />
      </label>
      <AuthSubmitButton pendingLabel="Sending reset link">Send reset link</AuthSubmitButton>
      <Link href="/login" className="focus-ring inline-block rounded-product text-sm font-semibold text-teal-700 hover:text-teal-550">
        Back to login
      </Link>
    </form>
  );
}
