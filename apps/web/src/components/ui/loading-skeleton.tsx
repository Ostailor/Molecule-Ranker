import { clsx } from "clsx";

export function LoadingSkeleton({
  lines = 3,
  className,
  label = "Loading content",
}: {
  lines?: number;
  className?: string;
  label?: string;
}) {
  return (
    <div role="status" aria-label={label} className={clsx("grid gap-3", className)}>
      {Array.from({ length: lines }).map((_, index) => (
        <div
          key={index}
          className={clsx("h-4 animate-pulse rounded-product bg-slatewash-200", index === lines - 1 ? "w-2/3" : "w-full")}
        />
      ))}
    </div>
  );
}
