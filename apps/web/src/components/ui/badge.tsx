import { clsx } from "clsx";

export type BadgeTone = "green" | "teal" | "amber" | "rose" | "gray";

const tones: Record<BadgeTone, string> = {
  green: "border-lime-450/30 bg-lime-350/20 text-lime-900",
  teal: "border-teal-450/25 bg-teal-450/10 text-teal-700",
  amber: "border-amber-450/25 bg-amber-450/10 text-amber-700",
  rose: "border-rose-200 bg-rose-50 text-rose-700",
  gray: "border-slatewash-200 bg-slatewash-100 text-ink-600",
};

export function Badge({
  children,
  tone = "gray",
  className,
}: {
  children: React.ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-product border px-2 py-1 text-xs font-semibold",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
