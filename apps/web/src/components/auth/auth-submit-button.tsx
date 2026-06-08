"use client";

import { Loader2 } from "lucide-react";
import { useFormStatus } from "react-dom";

export function AuthSubmitButton({ children, pendingLabel }: { children: React.ReactNode; pendingLabel: string }) {
  const { pending } = useFormStatus();

  return (
    <button
      className="focus-ring inline-flex h-9 w-full items-center justify-center gap-2 rounded-product border border-teal-550 bg-teal-550 px-3 text-sm font-semibold text-white transition hover:bg-teal-700 disabled:cursor-wait disabled:opacity-70"
      disabled={pending}
      type="submit"
    >
      {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
      <span>{pending ? pendingLabel : children}</span>
    </button>
  );
}
