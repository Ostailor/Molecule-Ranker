import { clsx } from "clsx";
import type { LucideIcon } from "lucide-react";

export function Metric({
  label,
  value,
  detail,
  icon: Icon,
  className,
}: {
  label: string;
  value: string;
  detail: string;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <div className={clsx("rounded-product border border-slatewash-200 bg-white p-4 shadow-line", className)}>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-ink-600">{label}</p>
        {Icon ? (
          <span className="flex h-8 w-8 items-center justify-center rounded-product bg-teal-450/10 text-teal-700">
            <Icon className="h-4 w-4" aria-hidden="true" />
          </span>
        ) : null}
      </div>
      <p className="mt-3 text-2xl font-semibold text-ink-950">{value}</p>
      <p className="mt-1 text-sm text-ink-600">{detail}</p>
    </div>
  );
}
