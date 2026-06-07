import { clsx } from "clsx";

export function UsageMeter({
  label,
  used,
  limit,
  className,
}: {
  label: string;
  used: number;
  limit: number;
  className?: string;
}) {
  const percentUsed = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;

  return (
    <div className={clsx("rounded-product border border-slatewash-200 bg-white p-4", className)}>
      <div className="flex items-center justify-between gap-4 text-sm">
        <span className="font-semibold text-ink-950">{label}</span>
        <span className="text-ink-600">
          {used} / {limit}
        </span>
      </div>
      <div className="mt-3 h-2 rounded-full bg-slatewash-100" aria-label={`${label} usage ${percentUsed}%`}>
        <div className="h-2 rounded-full bg-teal-550" style={{ width: `${percentUsed}%` }} />
      </div>
    </div>
  );
}
