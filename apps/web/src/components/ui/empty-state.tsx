import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";

export function EmptyState({
  title,
  description,
  actionHref,
  actionLabel,
  icon: Icon = Inbox,
}: {
  title: string;
  description: string;
  actionHref?: string;
  actionLabel?: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="rounded-product border border-dashed border-slatewash-200 bg-white p-6 text-center">
      <span className="mx-auto flex h-10 w-10 items-center justify-center rounded-product bg-slatewash-100 text-ink-500">
        <Icon className="h-5 w-5" aria-hidden="true" />
      </span>
      <h2 className="mt-3 text-base font-semibold text-ink-950">{title}</h2>
      <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-ink-600">{description}</p>
      {actionHref && actionLabel ? (
        <Button href={actionHref} variant="secondary" className="mt-4">
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
