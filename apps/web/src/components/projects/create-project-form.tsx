"use client";

import { useActionState } from "react";

import { AuthMessage } from "@/components/auth/auth-message";
import { AuthSubmitButton } from "@/components/auth/auth-submit-button";
import { initialAuthActionState } from "@/lib/supabase/auth-action-state";
import { createProjectAction } from "@/lib/supabase/project-actions";

export function CreateProjectForm() {
  const [state, formAction] = useActionState(createProjectAction, initialAuthActionState);

  return (
    <form action={formAction} className="grid gap-4" aria-label="Create project">
      <AuthMessage message={state.message} status={state.status} />
      <label>
        <span className="text-sm font-semibold text-ink-800">Project name</span>
        <input
          className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          maxLength={120}
          name="name"
          required
          type="text"
        />
      </label>
      <label>
        <span className="text-sm font-semibold text-ink-800">Research goal</span>
        <textarea
          className="mt-2 min-h-28 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
          maxLength={1000}
          name="research_goal"
        />
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <label>
          <span className="text-sm font-semibold text-ink-800">Disease or research area</span>
          <input
            className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
            maxLength={160}
            name="disease_focus"
            type="text"
          />
        </label>
        <label>
          <span className="text-sm font-semibold text-ink-800">Optional target focus</span>
          <input
            className="mt-2 w-full rounded-product border-slatewash-200 text-sm focus:border-teal-550 focus:ring-teal-550"
            maxLength={160}
            name="target_focus"
            type="text"
          />
        </label>
      </div>
      <AuthSubmitButton pendingLabel="Creating project">Create project</AuthSubmitButton>
    </form>
  );
}
