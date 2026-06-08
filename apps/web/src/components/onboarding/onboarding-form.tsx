"use client";

import { useActionState } from "react";

import { AuthMessage } from "@/components/auth/auth-message";
import { AuthSubmitButton } from "@/components/auth/auth-submit-button";
import { initialAuthActionState } from "@/lib/supabase/auth-action-state";
import { finishOnboardingAction } from "@/lib/supabase/auth-actions";

type OnboardingFormProps = {
  defaultDisplayName: string;
  organizationName?: string;
};

const useCases = [
  ["academic_researcher", "Academic researcher"],
  ["biotech_researcher", "Biotech researcher"],
  ["independent_researcher", "Independent researcher"],
  ["founder", "Founder"],
  ["other", "Other"],
] as const;

export function OnboardingForm({ defaultDisplayName, organizationName }: OnboardingFormProps) {
  const [state, formAction] = useActionState(finishOnboardingAction, initialAuthActionState);
  const hasOrganization = Boolean(organizationName);

  return (
    <form action={formAction} className="space-y-5" aria-label="Finish onboarding">
      <AuthMessage message={state.message} status={state.status} />

      <label className="flex items-start gap-3 rounded-product border border-amber-200 bg-amber-50 p-3">
        <input
          className="mt-1 rounded border-slatewash-200 text-teal-550 focus:ring-teal-550"
          name="researchUseAcknowledged"
          required
          type="checkbox"
        />
        <span className="text-sm leading-6 text-ink-700">
          I acknowledge MolCreate is for research-planning artifacts and hypotheses only. I will not enter
          patient-specific data, protected health information, payment details, treatment requests, synthesis plans, or wet-lab execution instructions.
        </span>
      </label>

      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Display name</span>
        <input
          autoComplete="name"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          defaultValue={defaultDisplayName}
          name="displayName"
          required
          type="text"
        />
      </label>

      <label className="block">
        <span className="text-sm font-semibold text-ink-800">Organization</span>
        <input
          autoComplete="organization"
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550 disabled:bg-slatewash-50 disabled:text-ink-600"
          defaultValue={organizationName ?? ""}
          disabled={hasOrganization}
          name="organizationName"
          required={!hasOrganization}
          type="text"
        />
      </label>
      {hasOrganization ? <input name="organizationName" type="hidden" value={organizationName} /> : null}

      <fieldset className="rounded-product border border-slatewash-200 p-3">
        <legend className="px-1 text-sm font-semibold text-ink-800">Role or use case</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {useCases.map(([value, label]) => (
            <label key={value} className="flex items-center gap-2 rounded-product border border-slatewash-200 bg-slatewash-50 p-3 text-sm text-ink-700">
              <input
                className="border-slatewash-200 text-teal-550 focus:ring-teal-550"
                name="useCase"
                required
                type="radio"
                value={value}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <AuthSubmitButton pendingLabel="Finishing onboarding">Finish onboarding</AuthSubmitButton>
    </form>
  );
}
