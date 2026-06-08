"use client";

import Link from "next/link";
import { useActionState } from "react";

import { initialAuthActionState } from "@/lib/supabase/auth-action-state";
import { signupAction } from "@/lib/supabase/auth-actions";
import { AuthMessage } from "./auth-message";
import { AuthSubmitButton } from "./auth-submit-button";

export function SignupForm() {
  const [state, formAction] = useActionState(signupAction, initialAuthActionState);

  return (
    <form action={formAction} className="space-y-4" aria-label="Create pilot account">
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
          autoComplete="new-password"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          minLength={8}
          name="password"
          required
          type="password"
        />
      </label>
      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Display name</span>
        <input
          autoComplete="name"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          name="displayName"
          required
          type="text"
        />
      </label>
      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Organization name</span>
        <input
          autoComplete="organization"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          name="organizationName"
          required
          type="text"
        />
      </label>
      <label className="flex items-start gap-3 rounded-product border border-slatewash-200 bg-slatewash-50 p-3">
        <input
          className="mt-1 rounded border-slatewash-200 text-teal-550 focus:ring-teal-550"
          name="researchUseAcknowledged"
          required
          type="checkbox"
        />
        <span className="text-sm leading-6 text-ink-700">
          I acknowledge MolCreate is for research-planning artifacts and hypotheses only, not medical advice,
          clinical decision support, patient-data workflows, synthesis planning, or lab protocols.
        </span>
      </label>
      <AuthSubmitButton pendingLabel="Creating account">Create account</AuthSubmitButton>
      <p className="text-sm text-ink-600">
        Already have access?{" "}
        <Link href="/login" className="focus-ring rounded-product font-semibold text-teal-700 hover:text-teal-550">
          Log in
        </Link>
      </p>
    </form>
  );
}
