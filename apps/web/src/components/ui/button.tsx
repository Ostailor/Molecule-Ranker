import Link from "next/link";
import { clsx } from "clsx";
import type { LucideIcon } from "lucide-react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<ButtonVariant, string> = {
  primary: "border-teal-550 bg-teal-550 text-white hover:bg-teal-700",
  secondary: "border-slatewash-200 bg-white text-ink-950 hover:border-teal-450/40 hover:bg-teal-450/5",
  ghost: "border-transparent bg-transparent text-ink-600 hover:bg-slatewash-100",
  danger: "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100",
};

type ButtonProps = {
  children: React.ReactNode;
  href?: string;
  icon?: LucideIcon;
  variant?: ButtonVariant;
  className?: string;
  type?: "button" | "submit";
};

export function Button({
  children,
  href,
  icon: Icon,
  variant = "primary",
  className,
  type = "button",
}: ButtonProps) {
  const classes = clsx(
    "focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-product border px-3 text-sm font-semibold transition",
    variants[variant],
    className,
  );

  const content = (
    <>
      {Icon ? <Icon className="h-4 w-4" aria-hidden="true" /> : null}
      <span>{children}</span>
    </>
  );

  if (href) {
    return (
      <Link href={href} className={classes}>
        {content}
      </Link>
    );
  }

  return (
    <button type={type} className={classes}>
      {content}
    </button>
  );
}
