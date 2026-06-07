import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from "lucide-react";
import { clsx } from "clsx";

type AlertTone = "info" | "warning" | "success" | "danger";

const toneStyles: Record<AlertTone, string> = {
  info: "border-teal-450/25 bg-teal-450/10 text-ink-800",
  warning: "border-amber-200 bg-amber-50 text-ink-800",
  success: "border-lime-450/30 bg-lime-350/20 text-ink-800",
  danger: "border-rose-200 bg-rose-50 text-ink-800",
};

const toneIcons = {
  info: Info,
  warning: ShieldAlert,
  success: CheckCircle2,
  danger: AlertTriangle,
};

export function Alert({
  title,
  children,
  tone = "info",
  className,
}: {
  title?: string;
  children: React.ReactNode;
  tone?: AlertTone;
  className?: string;
}) {
  const Icon = toneIcons[tone];

  return (
    <div role="status" className={clsx("rounded-product border p-3", toneStyles[tone], className)}>
      <div className="flex gap-3">
        <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div>
          {title ? <p className="text-sm font-semibold text-ink-950">{title}</p> : null}
          <div className="text-sm leading-6">{children}</div>
        </div>
      </div>
    </div>
  );
}
