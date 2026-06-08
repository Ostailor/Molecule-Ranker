"use client";

import Link from "next/link";
import { useActionState } from "react";

import { initialAuthActionState } from "@/lib/supabase/auth-action-state";
import { resetPasswordAction } from "@/lib/supabase/auth-actions";
import { AuthMessage } from "./auth-message";
import { AuthSubmitButton } from "./auth-submit-button";

export function ResetPasswordForm() {
  const [state, formAction] = useActionState(resetPasswordAction, initialAuthActionState);

  return (
    <form action={formAction} className="space-y-4" aria-label="Set new password">
      <AuthMessage message={state.message} status={state.status} />
      <label className="block">
        <span className="text-sm font-semibold text-ink-800">New password</span>
        <input
          autoComplete="new-password"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          minLength={8}
          name="password"
          required
          type="password"
        />
      </label>
      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Confirm password</span>
        <input
          autoComplete="new-password"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          minLength={8}
          name="confirmPassword"
          required
          type="password"
        />
      </label>
      <AuthSubmitButton pendingLabel="Updating password">Update password</AuthSubmitButton>
      {state.status === "success" ? (
        <Link href="/dashboard" className="focus-ring inline-block rounded-product text-sm font-semibold text-teal-700 hover:text-teal-550">
          Continue to dashboard
        </Link>
      ) : null}
    </form>
  );
}
