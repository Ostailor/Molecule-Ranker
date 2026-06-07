import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ErrorState({
  title,
  description,
  retryHref,
  retryLabel = "Try again",
}: {
  title: string;
  description: string;
  retryHref?: string;
  retryLabel?: string;
}) {
  return (
    <div role="alert" className="rounded-product border border-rose-200 bg-rose-50 p-4">
      <div className="flex gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-700" aria-hidden="true" />
        <div>
          <h2 className="text-base font-semibold text-ink-950">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-ink-700">{description}</p>
          {retryHref ? (
            <Button href={retryHref} variant="danger" className="mt-3">
              {retryLabel}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
