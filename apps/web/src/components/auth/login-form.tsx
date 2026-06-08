"use client";

import Link from "next/link";
import { useActionState } from "react";

import { initialAuthActionState } from "@/lib/supabase/auth-action-state";
import { loginAction } from "@/lib/supabase/auth-actions";
import { AuthMessage } from "./auth-message";
import { AuthSubmitButton } from "./auth-submit-button";

export function LoginForm({ next = "/dashboard" }: { next?: string }) {
  const [state, formAction] = useActionState(loginAction, initialAuthActionState);

  return (
    <form action={formAction} className="space-y-4" aria-label="Log in with email and password">
      <input name="next" type="hidden" value={next} />
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
      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Password</span>
        <input
          autoComplete="current-password"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          minLength={8}
          name="password"
          required
          type="password"
        />
      </label>
      <AuthSubmitButton pendingLabel="Signing in">Log in</AuthSubmitButton>
      <div className="flex flex-wrap justify-between gap-3 text-sm font-medium text-teal-700">
        <Link href="/forgot-password" className="focus-ring rounded-product hover:text-teal-550">
          Forgot password
        </Link>
        <Link href="/signup" className="focus-ring rounded-product hover:text-teal-550">
          Create account
        </Link>
      </div>
    </form>
  );
}
